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

import logging
import os
import pickle
from bisect import bisect_left, insort
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from beets import config

if TYPE_CHECKING:
    from beets.util import PathBytes


# Global logger.
log = logging.getLogger("beets")


@dataclass
class ImportState:
    """Representing the progress of an import task.

    Opens the state file on creation of the class. If you want
    to ensure the state is written to disk, you should use the
    context manager protocol.

    Tagprogress allows long tagging tasks to be resumed when they pause.

    Tagchoices persists the user's candidate selection for each album
    so that interrupted imports can skip re-asking for confirmation.

    Taghistory is a utility for manipulating the "incremental" import log.
    This keeps track of all directories that were ever imported, which
    allows the importer to only import new stuff.

    Lib_directory stores the library directory at the time the state was
    last written.  When the library directory has changed between
    sessions (e.g. the admin moved the root from ``/music/old`` to
    ``/music/new``), :meth:`rewrite_paths` can remap all stored paths.

    Usage
    -----
    ```
    # Readonly
    progress = ImportState().tagprogress

    # Read and write
    with ImportState() as state:
        state["key"] = "value"
    ```
    """

    tagprogress: dict[PathBytes, list[PathBytes]]
    taghistory: set[tuple[PathBytes, ...]]
    tagchoices: dict[tuple[PathBytes, tuple[PathBytes, ...]], dict]
    lib_directory: PathBytes | None
    path: PathBytes

    def __init__(self, readonly=False, path: PathBytes | None = None):
        self.path = path or os.fsencode(config["statefile"].as_filename())
        self.tagprogress = {}
        self.taghistory = set()
        self.tagchoices = {}
        self.lib_directory = None
        self._open()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._save()

    def _open(self):
        try:
            with open(self.path, "rb") as f:
                state = pickle.load(f)
                # Read the states
                self.tagprogress = state.get("tagprogress", {})
                self.taghistory = state.get("taghistory", set())
                self.tagchoices = state.get("tagchoices", {})
                self.lib_directory = state.get("lib_directory")
        except Exception as exc:
            # The `pickle` module can emit all sorts of exceptions during
            # unpickling, including ImportError. We use a catch-all
            # exception to avoid enumerating them all (the docs don't even have a
            # full list!).
            log.debug("state file could not be read: {}", exc)

    def _save(self):
        try:
            with open(self.path, "wb") as f:
                pickle.dump(
                    {
                        "tagprogress": self.tagprogress,
                        "taghistory": self.taghistory,
                        "tagchoices": self.tagchoices,
                        "lib_directory": self.lib_directory,
                    },
                    f,
                )
        except OSError as exc:
            log.error("state file could not be written: {}", exc)

    # -------------------------------- Tagprogress ------------------------------- #

    def progress_add(self, toppath: PathBytes, *paths: PathBytes):
        """Record that the files under all of the `paths` have been imported
        under `toppath`.
        """
        with self as state:
            imported = state.tagprogress.setdefault(toppath, [])
            for path in paths:
                if imported and imported[-1] <= path:
                    imported.append(path)
                else:
                    insort(imported, path)

    def progress_has_element(self, toppath: PathBytes, path: PathBytes) -> bool:
        """Return whether `path` has been imported in `toppath`."""
        imported = self.tagprogress.get(toppath, [])
        i = bisect_left(imported, path)
        return i != len(imported) and imported[i] == path

    def progress_has(self, toppath: PathBytes) -> bool:
        """Return `True` if there exist paths that have already been
        imported under `toppath`.
        """
        return toppath in self.tagprogress

    def progress_reset(self, toppath: PathBytes | None):
        """Reset the progress for `toppath` and also clear any saved
        choices under it (they only make sense together with progress).
        """
        with self as state:
            if toppath in state.tagprogress:
                del state.tagprogress[toppath]
            state.choices_forget_toppath(toppath)

    # -------------------------------- Taghistory -------------------------------- #

    def history_add(self, paths: list[PathBytes]):
        """Add the paths to the history."""
        with self as state:
            state.taghistory.add(tuple(paths))

    # -------------------------------- Tagchoices ------------------------------- #

    def choice_key(
        self, toppath: PathBytes | None, paths: Sequence[PathBytes]
    ) -> tuple[PathBytes, tuple[PathBytes, ...]]:
        """Build the key used to index ``tagchoices``."""
        return (toppath or b"", tuple(paths))

    def choice_set(
        self,
        toppath: PathBytes | None,
        paths: Sequence[PathBytes],
        choice_flag: str,
        match_info: dict | None = None,
        should_remove_duplicates: bool = False,
        should_merge_duplicates: bool = False,
    ):
        """Persist the user's choice for a task so that it can be
        restored after an import interruption without re-asking.
        """
        key = self.choice_key(toppath, paths)
        with self as state:
            state.tagchoices[key] = {
                "choice_flag": choice_flag,
                "match_info": match_info or {},
                "should_remove_duplicates": should_remove_duplicates,
                "should_merge_duplicates": should_merge_duplicates,
            }

    def choice_get(
        self, toppath: PathBytes | None, paths: Sequence[PathBytes]
    ) -> dict | None:
        """Return the previously-saved choice for a task or ``None``."""
        key = self.choice_key(toppath, paths)
        return self.tagchoices.get(key)

    def choice_clear(self, toppath: PathBytes | None, paths: Sequence[PathBytes]):
        """Remove a persisted choice once the task has finished."""
        key = self.choice_key(toppath, paths)
        with self as state:
            state.tagchoices.pop(key, None)

    def choices_forget_toppath(self, toppath: PathBytes | None):
        """Remove all persisted choices under the given ``toppath``.

        Used when the user declines to resume or when a toppath is
        fully processed.
        """
        with self as state:
            keys = [
                k for k in state.tagchoices if k[0] == (toppath or b"")
            ]
            for k in keys:
                del state.tagchoices[k]

    # --------------------------- Library directory ---------------------------- #

    def save_lib_directory(self, directory: PathBytes):
        """Persist the current library directory so that future sessions
        can detect when it has changed.
        """
        with self as state:
            state.lib_directory = directory

    def rewrite_paths(self, old_prefix: PathBytes, new_prefix: PathBytes):
        """Replace ``old_prefix`` with ``new_prefix`` in every path stored
        in ``tagprogress`` and ``tagchoices``.

        This is used when the library root directory has been moved
        (e.g. from ``/music/old`` to ``/music/new``) between import
        sessions.  Without rewriting, ``already_imported`` would fail
        to match the new paths and albums would be re-processed.

        The method also updates ``lib_directory`` to ``new_prefix``.
        """
        if old_prefix == new_prefix:
            return

        def _rewrite(p: PathBytes) -> PathBytes:
            if p.startswith(old_prefix):
                return new_prefix + p[len(old_prefix):]
            return p

        with self as state:
            new_progress: dict[PathBytes, list[PathBytes]] = {}
            for toppath, paths in state.tagprogress.items():
                new_toppath = _rewrite(toppath)
                new_paths = [_rewrite(p) for p in paths]
                new_progress[new_toppath] = new_paths
            state.tagprogress = new_progress

            new_choices: dict[tuple[PathBytes, tuple[PathBytes, ...]], dict] = {}
            for (toppath, paths_tuple), value in state.tagchoices.items():
                new_toppath = _rewrite(toppath)
                new_paths_tuple = tuple(_rewrite(p) for p in paths_tuple)
                new_choices[(new_toppath, new_paths_tuple)] = value
            state.tagchoices = new_choices

            state.lib_directory = new_prefix
