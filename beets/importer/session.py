# This file is part of beets.
# Copyright 2016, Adrian Sampson.
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from beets import config, logging, plugins, util
from beets.importer.tasks import Action
from beets.util import displayable_path, normpath, pipeline, syspath

from . import stages as stagefuncs
from .state import ImportState

if TYPE_CHECKING:
    from collections.abc import Sequence

    from beets import dbcore, library
    from beets.util import PathBytes

    from .tasks import ImportTask


QUEUE_SIZE = 128


@dataclass
class DuplicateConflict:
    """Represents a duplicate conflict detected during import."""

    task: ImportTask
    found_duplicates: list
    similarity: float = 0.0
    suggested_action: str = "ask"
    resolved: bool = False
    resolution: str | None = None


@dataclass
class ConflictQueue:
    """A queue to collect duplicate conflicts for batch processing."""

    conflicts: list[DuplicateConflict] = field(default_factory=list)

    def add(self, conflict: DuplicateConflict) -> None:
        """Add a conflict to the queue."""
        self.conflicts.append(conflict)

    def __len__(self) -> int:
        return len(self.conflicts)

    def __iter__(self):
        return iter(self.conflicts)

    def pending(self) -> list[DuplicateConflict]:
        """Return all unresolved conflicts."""
        return [c for c in self.conflicts if not c.resolved]

# Global logger.
log = logging.getLogger("beets")


class ImportAbortError(Exception):
    """Raised when the user aborts the tagging operation."""

    pass


class ImportSession:
    """Controls an import action. Subclasses should implement methods to
    communicate with the user or otherwise make decisions.
    """

    logger: logging.Logger
    paths: list[PathBytes]
    lib: library.Library

    _is_resuming: dict[bytes, bool]
    _merged_items: set[PathBytes]
    _merged_dirs: set[PathBytes]
    conflict_queue: ConflictQueue

    def __init__(
        self,
        lib: library.Library,
        loghandler: logging.Handler | None,
        paths: Sequence[PathBytes] | None,
        query: dbcore.Query | None,
    ):
        """Create a session.

        Parameters
        ----------
        lib : library.Library
            The library instance to which items will be imported.
        loghandler : logging.Handler or None
            A logging handler to use for the session's logger. If None, a
            NullHandler will be used.
        paths : os.PathLike or None
            The paths to be imported.
        query : dbcore.Query or None
            A query to filter items for import.
        """
        self.lib = lib
        self.logger = self._setup_logging(loghandler)
        self.query = query
        self._is_resuming = {}
        self._merged_items = set()
        self._merged_dirs = set()
        self.conflict_queue = ConflictQueue()

        # Normalize the paths.
        self.paths = list(map(normpath, paths or []))

    def _setup_logging(self, loghandler: logging.Handler | None):
        logger = logging.getLogger(__name__)
        logger.propagate = False
        if not loghandler:
            loghandler = logging.NullHandler()
        logger.handlers = [loghandler]
        return logger

    def set_config(self, config):
        """Set `config` property from global import config and make
        implied changes.
        """
        # FIXME: Maybe this function should not exist and should instead
        # provide "decision wrappers" like "should_resume()", etc.
        iconfig = dict(config)
        self.config = iconfig

        # Incremental and progress are mutually exclusive.
        if iconfig["incremental"]:
            iconfig["resume"] = False

        # When based on a query instead of directories, never
        # save progress or try to resume.
        if self.query is not None:
            iconfig["resume"] = False
            iconfig["incremental"] = False

        if iconfig["reflink"]:
            iconfig["reflink"] = iconfig["reflink"].as_choice(
                ["auto", True, False]
            )

        # Copy, move, reflink, link, and hardlink are mutually exclusive.
        if iconfig["move"]:
            iconfig["copy"] = False
            iconfig["link"] = False
            iconfig["hardlink"] = False
            iconfig["reflink"] = False
        elif iconfig["link"]:
            iconfig["copy"] = False
            iconfig["move"] = False
            iconfig["hardlink"] = False
            iconfig["reflink"] = False
        elif iconfig["hardlink"]:
            iconfig["copy"] = False
            iconfig["move"] = False
            iconfig["link"] = False
            iconfig["reflink"] = False
        elif iconfig["reflink"]:
            iconfig["copy"] = False
            iconfig["move"] = False
            iconfig["link"] = False
            iconfig["hardlink"] = False

        # Only delete when copying.
        if not iconfig["copy"]:
            iconfig["delete"] = False

        self.want_resume = config["resume"].as_choice([True, False, "ask"])

    def tag_log(self, status, paths: Sequence[PathBytes]):
        """Log a message about a given album to the importer log. The status
        should reflect the reason the album couldn't be tagged.
        """
        self.logger.info("{} {}", status, displayable_path(paths))

    def log_choice(self, task: ImportTask, duplicate=False):
        """Logs the task's current choice if it should be logged. If
        ``duplicate``, then this is a secondary choice after a duplicate was
        detected and a decision was made.
        """
        paths = task.paths
        if duplicate:
            # Duplicate: log all three choices (skip, keep both, and trump).
            if task.should_remove_duplicates:
                self.tag_log("duplicate-replace", paths)
            elif task.choice_flag in (Action.ASIS, Action.APPLY):
                self.tag_log("duplicate-keep", paths)
            elif task.choice_flag is Action.SKIP:
                self.tag_log("duplicate-skip", paths)
        else:
            # Non-duplicate: log "skip" and "asis" choices.
            if task.choice_flag is Action.ASIS:
                self.tag_log("asis", paths)
            elif task.choice_flag is Action.SKIP:
                self.tag_log("skip", paths)

    def should_resume(self, path: PathBytes):
        raise NotImplementedError

    def choose_match(self, task: ImportTask):
        raise NotImplementedError

    def resolve_duplicate(self, task: ImportTask, found_duplicates):
        raise NotImplementedError

    def resolve_duplicate_batch(self, conflicts: list[DuplicateConflict]):
        """Resolve multiple duplicate conflicts in batch.

        Subclasses should implement this method to provide a batch
        resolution interface. The method should iterate over the conflicts
        and set their `resolved` and `resolution` attributes, and update
        the corresponding tasks accordingly.
        """
        raise NotImplementedError

    def choose_item(self, task: ImportTask):
        raise NotImplementedError

    def run(self):
        """Run the import task."""
        self.logger.info("import started {}", time.asctime())
        self.set_config(config["import"])

        # Set up the pipeline.
        if self.query is None:
            stages = [stagefuncs.read_tasks(self)]
        else:
            stages = [stagefuncs.query_tasks(self)]

        # In pretend mode, just log what would otherwise be imported.
        if self.config["pretend"]:
            stages += [stagefuncs.log_files(self)]
        else:
            if self.config["group_albums"] and not self.config["singletons"]:
                # Split directory tasks into one task for each album.
                stages += [stagefuncs.group_albums(self)]

            # These stages either talk to the user to get a decision or,
            # in the case of a non-autotagged import, just choose to
            # import everything as-is. In *both* cases, these stages
            # also add the music to the library database, so later
            # stages need to read and write data from there.
            if self.config["autotag"]:
                stages += [
                    stagefuncs.lookup_candidates(self),
                    stagefuncs.user_query(self),
                ]
            else:
                stages += [stagefuncs.import_asis(self)]

            # Plugin stages.
            for stage_func in plugins.early_import_stages():
                stages.append(stagefuncs.plugin_stage(self, stage_func))
            for stage_func in plugins.import_stages():
                stages.append(stagefuncs.plugin_stage(self, stage_func))

            stages += [stagefuncs.manipulate_files(self)]

        pl = pipeline.Pipeline(stages)

        # Run the pipeline.
        plugins.send("import_begin", session=self)
        try:
            if config["threaded"]:
                pl.run_parallel(QUEUE_SIZE)
            else:
                pl.run_sequential()
        except ImportAbortError:
            # User aborted operation. Silently stop.
            pass

        # Process any pending duplicate conflicts in batch mode
        pending_conflicts = self.conflict_queue.pending()
        if pending_conflicts:
            log.info(
                "Found {} pending duplicate conflicts, starting batch resolution...",
                len(pending_conflicts),
            )
            self.resolve_duplicate_batch(pending_conflicts)

            # After batch resolution, re-run affected tasks through the pipeline
            resolved_tasks = []
            for conflict in pending_conflicts:
                if conflict.resolved and conflict.resolution != "skip":
                    resolved_tasks.append(conflict.task)

            if resolved_tasks:
                log.info(
                    "Re-processing {} resolved tasks...", len(resolved_tasks)
                )
                self._process_resolved_tasks(resolved_tasks)

    def _process_resolved_tasks(self, tasks: list[ImportTask]):
        """Process tasks that have had their duplicate conflicts resolved."""
        from . import stages as stagefuncs

        # Reset the temporary skip status for tasks that should be processed
        for task in tasks:
            # Remove the batch queued marker
            if hasattr(task, "_batch_queued"):
                delattr(task, "_batch_queued")
            # Restore choice flag based on resolution
            # (keep/remove/merge keep the previous set choice during resolution)
            # The task choice flag was set to SKIP temporarily;
            # for keep (k) and remove (r) and merge (m), we need to reset to
            # the appropriate action. For skip (s), it stays as SKIP.

        @pipeline.stage
        def apply_resolved_choice(session: ImportSession, task: ImportTask):
            """Apply the choice that was set during batch resolution."""
            # task.should_remove_duplicates, task.should_merge_duplicates,
            # and task.choice_flag were already set during batch resolution
            stagefuncs._apply_choice(session, task)
            return task

        resolve_stages = [
            iter(tasks),
        ]

        for stage_func in plugins.early_import_stages():
            resolve_stages.append(stagefuncs.plugin_stage(self, stage_func))
        for stage_func in plugins.import_stages():
            resolve_stages.append(stagefuncs.plugin_stage(self, stage_func))

        resolve_stages += [
            apply_resolved_choice(self),
            stagefuncs.manipulate_files(self),
        ]

        pl = pipeline.Pipeline(resolve_stages)
        try:
            if config["threaded"]:
                pl.run_parallel(QUEUE_SIZE)
            else:
                pl.run_sequential()
        except ImportAbortError:
            pass

    # Incremental and resumed imports

    def already_imported(self, toppath: PathBytes, paths: Sequence[PathBytes]):
        """Returns true if the files belonging to this task have already
        been imported in a previous session.
        """
        if self.is_resuming(toppath) and all(
            [ImportState().progress_has_element(toppath, p) for p in paths]
        ):
            return True
        if self.config["incremental"] and tuple(paths) in self.history_dirs:
            return True

        return False

    _history_dirs = None

    @property
    def history_dirs(self) -> set[tuple[PathBytes, ...]]:
        # FIXME: This could be simplified to a cached property
        if self._history_dirs is None:
            self._history_dirs = ImportState().taghistory
        return self._history_dirs

    def already_merged(self, paths: Sequence[PathBytes]):
        """Returns true if all the paths being imported were part of a merge
        during previous tasks.
        """
        for path in paths:
            if path not in self._merged_items and path not in self._merged_dirs:
                return False
        return True

    def mark_merged(self, paths: Sequence[PathBytes]):
        """Mark paths and directories as merged for future reimport tasks."""
        self._merged_items.update(paths)
        dirs = {
            os.path.dirname(path) if os.path.isfile(syspath(path)) else path
            for path in paths
        }
        self._merged_dirs.update(dirs)

    def is_resuming(self, toppath: PathBytes):
        """Return `True` if user wants to resume import of this path.

        You have to call `ask_resume` first to determine the return value.
        """
        return self._is_resuming.get(toppath, False)

    def ask_resume(self, toppath: PathBytes):
        """If import of `toppath` was aborted in an earlier session, ask
        user if they want to resume the import.

        Determines the return value of `is_resuming(toppath)`.
        """
        if self.want_resume and ImportState().progress_has(toppath):
            # Either accept immediately or prompt for input to decide.
            if self.want_resume is True or self.should_resume(toppath):
                log.warning(
                    "Resuming interrupted import of {}",
                    util.displayable_path(toppath),
                )
                self._is_resuming[toppath] = True
            else:
                # Clear progress; we're starting from the top.
                ImportState().progress_reset(toppath)
