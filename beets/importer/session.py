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
from collections import defaultdict
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

# Global logger.
log = logging.getLogger("beets")


@dataclass
class ImportAuditSummary:
    """汇总导入过程中各类操作的审计信息。

    用于追踪导入期间哪些条目被修改、跳过或需人工审核，
    便于导入结束后回顾结果和后续处理。
    """

    applied: int = 0
    applied_details: list[tuple[str, str]] = field(default_factory=list)

    asis: int = 0
    asis_details: list[tuple[str, str]] = field(default_factory=list)

    skipped: int = 0
    skipped_details: list[tuple[str, str]] = field(default_factory=list)

    duplicate_replace: int = 0
    duplicate_replace_details: list[tuple[str, str]] = field(default_factory=list)

    duplicate_keep: int = 0
    duplicate_keep_details: list[tuple[str, str]] = field(default_factory=list)

    duplicate_skip: int = 0
    duplicate_skip_details: list[tuple[str, str]] = field(default_factory=list)

    needs_review: int = 0
    needs_review_details: list[tuple[str, str]] = field(default_factory=list)

    total_tasks: int = 0

    def record_choice(
        self,
        task: ImportTask,
        duplicate: bool = False,
        needs_review: bool = False,
    ):
        """记录任务的选择结果到审计摘要中。

        Parameters
        ----------
        task : ImportTask
            已做出选择的导入任务
        duplicate : bool
            是否为重复处理的二次选择
        needs_review : bool
            是否需要人工审核（如用户手动做出决策）
        """
        from .tasks import SentinelImportTask

        if isinstance(task, SentinelImportTask):
            return

        path_display = displayable_path(task.paths)
        desc = self._task_description(task)

        if duplicate:
            if task.should_remove_duplicates:
                self.duplicate_replace += 1
                self.duplicate_replace_details.append((path_display, desc))
            elif task.choice_flag in (Action.ASIS, Action.APPLY):
                self.duplicate_keep += 1
                self.duplicate_keep_details.append((path_display, desc))
            elif task.choice_flag is Action.SKIP:
                self.duplicate_skip += 1
                self.duplicate_skip_details.append((path_display, desc))
        else:
            if task.choice_flag is Action.APPLY:
                self.applied += 1
                self.applied_details.append((path_display, desc))
            elif task.choice_flag is Action.ASIS:
                self.asis += 1
                self.asis_details.append((path_display, desc))
            elif task.choice_flag is Action.SKIP:
                self.skipped += 1
                self.skipped_details.append((path_display, desc))

        if needs_review:
            self.needs_review += 1
            self.needs_review_details.append((path_display, desc))

        self.total_tasks += 1

    @staticmethod
    def _task_description(task: ImportTask) -> str:
        """生成任务的简要描述（艺术家/专辑或标题）。

        优先使用已选择的匹配信息；若尚不可用则回退到现有元数据；
        任何异常均被捕获并返回一个占位值，避免审计路径崩溃。
        """
        try:
            if task.is_album:
                info = task.chosen_info() if task.choice_flag else {}
                artist = (
                    info.get("artist")
                    or info.get("albumartist")
                    or (task.items and task.items[0].albumartist)
                    or (task.items and task.items[0].artist)
                    or "Unknown"
                )
                album = (
                    info.get("album")
                    or task.cur_album
                    or (task.items and task.items[0].album)
                    or "Unknown"
                )
                return f"{artist} - {album}"
            else:
                info = task.chosen_info() if task.choice_flag else {}
                item = getattr(task, "item", None)
                artist = (
                    info.get("artist")
                    or (item and item.artist)
                    or "Unknown"
                )
                title = (
                    info.get("title")
                    or (item and item.title)
                    or "Unknown"
                )
                return f"{artist} - {title}"
        except Exception:
            return "N/A"


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
    audit_summary: ImportAuditSummary

    _is_resuming: dict[bytes, bool]
    _merged_items: set[PathBytes]
    _merged_dirs: set[PathBytes]

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
        self.audit_summary = ImportAuditSummary()

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

    def log_choice(self, task: ImportTask, duplicate=False, needs_review=False):
        """Logs the task's current choice if it should be logged. If
        ``duplicate``, then this is a secondary choice after a duplicate was
        detected and a decision was made.

        Also records the choice in the audit summary (including APPLY
        actions, which are not written to the tag log file).
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
            # Note: APPLY is intentionally not logged here (per existing
            # behavior), but it IS recorded in the audit summary below.

        self.audit_summary.record_choice(task, duplicate, needs_review)

    def should_resume(self, path: PathBytes):
        raise NotImplementedError

    def choose_match(self, task: ImportTask):
        raise NotImplementedError

    def resolve_duplicate(self, task: ImportTask, found_duplicates):
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

        self._emit_audit_summary()

    def _emit_audit_summary(self):
        """在导入结束时发送审计摘要事件并记录到日志。

        子类可以覆盖 print_audit_summary 方法提供特定UI的输出。
        """
        summary = self.audit_summary
        if summary.total_tasks > 0:
            plugins.send("import_audit_summary", session=self, summary=summary)
            self.print_audit_summary(summary)
            self._log_audit_summary(summary)

    def print_audit_summary(self, summary: ImportAuditSummary):
        """打印审计摘要到控制台。

        基类实现使用标准日志。子类（如 TerminalImportSession）可以
        覆盖此方法以提供更友好的终端输出。
        """
        pass

    def _log_audit_summary(self, summary: ImportAuditSummary):
        """将审计摘要写入导入日志文件。"""
        self.logger.info("")
        self.logger.info("=== Import Audit Summary ===")
        self.logger.info("Total tasks processed: {}", summary.total_tasks)
        if summary.applied:
            self.logger.info("  Applied metadata: {}", summary.applied)
        if summary.asis:
            self.logger.info("  Imported as-is: {}", summary.asis)
        if summary.skipped:
            self.logger.info("  Skipped: {}", summary.skipped)
        if summary.duplicate_replace:
            self.logger.info(
                "  Duplicates (replaced old): {}", summary.duplicate_replace
            )
        if summary.duplicate_keep:
            self.logger.info(
                "  Duplicates (kept both): {}", summary.duplicate_keep
            )
        if summary.duplicate_skip:
            self.logger.info(
                "  Duplicates (skipped new): {}", summary.duplicate_skip
            )
        if summary.needs_review:
            self.logger.info(
                "  Required manual review: {}", summary.needs_review
            )
        self.logger.info("============================")
        self.logger.info("import finished {}", time.asctime())

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
