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
import time
import uuid
from bisect import bisect_left, insort
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from beets import config

if TYPE_CHECKING:
    from collections.abc import Sequence

    from beets import library
    from beets.autotag.hooks import AlbumMatch, TrackMatch
    from beets.autotag.match import Recommendation
    from beets.util import PathBytes


# Global logger.
log = logging.getLogger("beets")


class TaskStage(Enum):
    """Enumeration of possible stages for an import task."""

    CREATED = "CREATED"
    LOOKUP = "LOOKUP"
    CHOICE = "CHOICE"
    DUPLICATE_RESOLUTION = "DUPLICATE_RESOLUTION"
    METADATA_APPLY = "METADATA_APPLY"
    ADD = "ADD"
    MANIPULATE_FILES = "MANIPULATE_FILES"
    FINALIZE = "FINALIZE"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


@dataclass
class TaskSnapshot:
    """A snapshot of the state of a single import task.

    This captures all the intermediate state of an ImportTask at any
    given point in time, enabling:
    - Exception recovery by restoring task state from a snapshot
    - Debugging and troubleshooting by inspecting the full task state
    - Progress tracking and resumption of interrupted imports
    """

    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    toppath: PathBytes | None = None
    paths: list[PathBytes] = field(default_factory=list)
    is_album: bool = True
    is_singleton: bool = False
    is_sentinel: bool = False
    is_archive: bool = False

    stage: TaskStage = TaskStage.CREATED
    error_message: str | None = None
    error_traceback: str | None = None

    choice_flag: str | None = None
    cur_artist: str | None = None
    cur_album: str | None = None
    rec: str | None = None
    candidates_count: int = 0

    should_remove_duplicates: bool = False
    should_merge_duplicates: bool = False

    replaced_items_count: int = 0
    replaced_albums_count: int = 0
    old_paths: list[PathBytes] = field(default_factory=list)

    items_count: int = 0
    imported_items_count: int = 0

    album_id: int | None = None
    archive_extracted: bool = False
    archive_path: PathBytes | None = None

    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def mark_stage(self, stage: TaskStage) -> None:
        """Update the current stage and timestamp."""
        self.stage = stage
        self.updated_at = time.time()

    def mark_error(self, error_message: str, error_traceback: str | None = None) -> None:
        """Mark the task as errored with diagnostic information."""
        self.stage = TaskStage.ERROR
        self.error_message = error_message
        self.error_traceback = error_traceback
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the snapshot to a dictionary for debugging/logging."""
        return {
            "task_id": self.task_id,
            "toppath": os.fsdecode(self.toppath) if self.toppath else None,
            "paths": [os.fsdecode(p) for p in self.paths],
            "is_album": self.is_album,
            "is_singleton": self.is_singleton,
            "is_sentinel": self.is_sentinel,
            "is_archive": self.is_archive,
            "stage": self.stage.value,
            "error_message": self.error_message,
            "choice_flag": self.choice_flag,
            "cur_artist": self.cur_artist,
            "cur_album": self.cur_album,
            "rec": self.rec,
            "candidates_count": self.candidates_count,
            "should_remove_duplicates": self.should_remove_duplicates,
            "should_merge_duplicates": self.should_merge_duplicates,
            "items_count": self.items_count,
            "imported_items_count": self.imported_items_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class SessionSnapshot:
    """A snapshot of the state of the entire import session.

    Captures session-level state including which paths are being resumed,
    which items/dirs have been merged, and task progress. This enables
    full session recovery after a crash or interruption.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None

    paths: list[PathBytes] = field(default_factory=list)
    query: str | None = None

    is_resuming: dict[bytes, bool] = field(default_factory=dict)
    merged_items: set[PathBytes] = field(default_factory=set)
    merged_dirs: set[PathBytes] = field(default_factory=set)

    task_snapshots: dict[str, TaskSnapshot] = field(default_factory=dict)

    total_tasks: int = 0
    completed_tasks: int = 0
    skipped_tasks: int = 0
    errored_tasks: int = 0

    config_snapshot: dict[str, Any] = field(default_factory=dict)

    def record_task(self, snapshot: TaskSnapshot) -> None:
        """Record or update a task snapshot in the session."""
        self.task_snapshots[snapshot.task_id] = snapshot
        self._update_counts()

    def _update_counts(self) -> None:
        self.total_tasks = len(self.task_snapshots)
        self.completed_tasks = sum(
            1 for t in self.task_snapshots.values() if t.stage == TaskStage.COMPLETED
        )
        self.skipped_tasks = sum(
            1 for t in self.task_snapshots.values() if t.stage == TaskStage.SKIPPED
        )
        self.errored_tasks = sum(
            1 for t in self.task_snapshots.values() if t.stage == TaskStage.ERROR
        )

    def active_tasks(self) -> list[TaskSnapshot]:
        """Return tasks that are not yet completed/skipped/errored."""
        return [
            t
            for t in self.task_snapshots.values()
            if t.stage
            not in (TaskStage.COMPLETED, TaskStage.SKIPPED, TaskStage.ERROR)
        ]

    def mark_complete(self) -> None:
        """Mark the session as completed."""
        self.end_time = time.time()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the session snapshot for debugging/logging."""
        return {
            "session_id": self.session_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "paths": [os.fsdecode(p) for p in self.paths],
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "skipped_tasks": self.skipped_tasks,
            "errored_tasks": self.errored_tasks,
            "active_tasks": [t.to_dict() for t in self.active_tasks()],
            "duration": (self.end_time - self.start_time) if self.end_time else None,
        }


@dataclass
class FactorySnapshot:
    """A snapshot of the state of an ImportTaskFactory.

    Tracks statistics about tasks generated by a factory, including
    skipped paths and imported counts.
    """

    toppath: PathBytes | None = None
    is_archive: bool = False
    skipped: int = 0
    imported: int = 0
    created_at: float = field(default_factory=time.time)


@dataclass
class ImportState:
    """Representing the progress of an import task.

    Opens the state file on creation of the class. If you want
    to ensure the state is written to disk, you should use the
    context manager protocol.

    Tagprogress allows long tagging tasks to be resumed when they pause.

    Taghistory is a utility for manipulating the "incremental" import log.
    This keeps track of all directories that were ever imported, which
    allows the importer to only import new stuff.

    The unified snapshot model (task_snapshots, session_snapshot,
    factory_snapshots) consolidates previously-scattered state variables
    into a coherent, serializable structure. This enables:
    - Crash recovery: resume interrupted imports at the task level
    - Diagnostics: inspect exactly which stage each task reached
    - Debugging: full state dump for troubleshooting

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
    task_snapshots: dict[str, TaskSnapshot]
    session_snapshot: SessionSnapshot | None
    factory_snapshots: dict[bytes, FactorySnapshot]
    path: PathBytes

    def __init__(self, readonly=False, path: PathBytes | None = None):
        self.path = path or os.fsencode(config["statefile"].as_filename())
        self.tagprogress = {}
        self.taghistory = set()
        self.task_snapshots = {}
        self.session_snapshot = None
        self.factory_snapshots = {}
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
                # Read unified snapshots (with backward-compatible defaults)
                self.task_snapshots = state.get("task_snapshots", {})
                self.session_snapshot = state.get("session_snapshot", None)
                self.factory_snapshots = state.get("factory_snapshots", {})
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
                        "task_snapshots": self.task_snapshots,
                        "session_snapshot": self.session_snapshot,
                        "factory_snapshots": self.factory_snapshots,
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
        """Reset the progress for `toppath`."""
        with self as state:
            if toppath in state.tagprogress:
                del state.tagprogress[toppath]

    # -------------------------------- Taghistory -------------------------------- #

    def history_add(self, paths: list[PathBytes]):
        """Add the paths to the history."""
        with self as state:
            state.taghistory.add(tuple(paths))

    # ----------------------------- Task Snapshots ------------------------------ #

    def save_task_snapshot(self, snapshot: TaskSnapshot):
        """Persist a task snapshot to the state file.

        This should be called at critical points during the import pipeline
        (e.g., after each stage completes) so that a crash can be recovered
        from with fine granularity.
        """
        with self as state:
            state.task_snapshots[snapshot.task_id] = snapshot

    def get_task_snapshot(self, task_id: str) -> TaskSnapshot | None:
        """Retrieve a task snapshot by ID, or None if not found."""
        return self.task_snapshots.get(task_id)

    def clear_task_snapshots(self, toppath: PathBytes | None = None):
        """Clear task snapshots. If `toppath` is given, only clear
        snapshots for tasks under that top-level path.
        """
        with self as state:
            if toppath is None:
                state.task_snapshots = {}
            else:
                state.task_snapshots = {
                    tid: snap
                    for tid, snap in state.task_snapshots.items()
                    if snap.toppath != toppath
                }

    def get_active_task_snapshots(self, toppath: PathBytes | None = None) -> list[TaskSnapshot]:
        """Return all task snapshots that are not in a terminal state.

        If `toppath` is given, restrict to tasks under that path.
        This is the primary entry point for crash recovery: call this
        after loading the state to discover which tasks need to be
        re-run.
        """
        terminal = {TaskStage.COMPLETED, TaskStage.SKIPPED}
        results = [
            snap
            for snap in self.task_snapshots.values()
            if snap.stage not in terminal
        ]
        if toppath is not None:
            results = [s for s in results if s.toppath == toppath]
        return results

    # ---------------------------- Session Snapshots ---------------------------- #

    def save_session_snapshot(self, snapshot: SessionSnapshot):
        """Persist the session-wide snapshot."""
        with self as state:
            state.session_snapshot = snapshot

    def get_session_snapshot(self) -> SessionSnapshot | None:
        """Retrieve the last saved session snapshot, or None."""
        return self.session_snapshot

    def clear_session_snapshot(self):
        """Clear the session snapshot (e.g., on successful completion)."""
        with self as state:
            state.session_snapshot = None

    # ---------------------------- Factory Snapshots ---------------------------- #

    def save_factory_snapshot(self, toppath: PathBytes, snapshot: FactorySnapshot):
        """Persist a factory snapshot keyed by its top-level path."""
        with self as state:
            state.factory_snapshots[toppath] = snapshot

    def get_factory_snapshot(self, toppath: PathBytes) -> FactorySnapshot | None:
        """Retrieve a factory snapshot by top-level path, or None."""
        return self.factory_snapshots.get(toppath)

    def clear_factory_snapshots(self, toppath: PathBytes | None = None):
        """Clear factory snapshots. If `toppath` is given, only that one."""
        with self as state:
            if toppath is None:
                state.factory_snapshots = {}
            elif toppath in state.factory_snapshots:
                del state.factory_snapshots[toppath]

    # ------------------------------- Diagnostics ------------------------------- #

    def diagnostic_dump(self) -> dict[str, Any]:
        """Return a structured dict describing the full import state.

        Useful for logging, crash reports, and interactive debugging.
        The output is fully JSON-serialisable (no bytes, no objects).
        """
        session = (
            self.session_snapshot.to_dict() if self.session_snapshot else None
        )
        tasks = [snap.to_dict() for snap in self.task_snapshots.values()]
        factories = [
            {
                "toppath": os.fsdecode(t) if t else None,
                "is_archive": snap.is_archive,
                "skipped": snap.skipped,
                "imported": snap.imported,
                "created_at": snap.created_at,
            }
            for t, snap in self.factory_snapshots.items()
        ]
        return {
            "session": session,
            "tasks": tasks,
            "factories": factories,
            "tagprogress_keys": [
                os.fsdecode(k) for k in self.tagprogress.keys()
            ],
            "taghistory_count": len(self.taghistory),
        }
