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

import json
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

# Schema version numbers for each snapshot type.
# Increment these whenever the schema changes in a non-backward-compatible way.
TASK_SNAPSHOT_SCHEMA_VERSION = 1
SESSION_SNAPSHOT_SCHEMA_VERSION = 1
FACTORY_SNAPSHOT_SCHEMA_VERSION = 1


class SnapshotMigrationError(Exception):
    """Raised when a snapshot cannot be migrated from an old schema version."""

    pass


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

    Schema Versioning
    -----------------
    Each snapshot carries a ``schema_version``. When the schema changes,
    increment ``TASK_SNAPSHOT_SCHEMA_VERSION`` and implement a
    migration in :meth:`from_dict`. Old snapshots from disk are
    automatically migrated on load.

    Transaction Consistency
    -----------------------
    ``last_tx_id`` and ``tx_committed`` track the relationship between
    this snapshot and database transactions. A snapshot should only be
    persisted to disk **after** the corresponding database transaction
    has committed. On recovery, ``verify_consistency`` checks that the
    database state matches what the snapshot claims.
    """

    schema_version: int = TASK_SNAPSHOT_SCHEMA_VERSION

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

    last_tx_id: str | None = None
    tx_committed: bool = False

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

    def mark_tx_boundary(self, tx_id: str, committed: bool) -> None:
        """Record the relationship with a database transaction.

        Call this **after** the transaction has committed or rolled back.
        ``committed`` should be True only if the transaction was
        successfully committed to the database.
        """
        self.last_tx_id = tx_id
        self.tx_committed = committed
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the snapshot to a JSON-compatible dictionary.

        All bytes are decoded to strings, and enums to their values.
        """
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "toppath": os.fsdecode(self.toppath) if self.toppath else None,
            "paths": [os.fsdecode(p) for p in self.paths],
            "is_album": self.is_album,
            "is_singleton": self.is_singleton,
            "is_sentinel": self.is_sentinel,
            "is_archive": self.is_archive,
            "stage": self.stage.value,
            "error_message": self.error_message,
            "error_traceback": self.error_traceback,
            "choice_flag": self.choice_flag,
            "cur_artist": self.cur_artist,
            "cur_album": self.cur_album,
            "rec": self.rec,
            "candidates_count": self.candidates_count,
            "should_remove_duplicates": self.should_remove_duplicates,
            "should_merge_duplicates": self.should_merge_duplicates,
            "replaced_items_count": self.replaced_items_count,
            "replaced_albums_count": self.replaced_albums_count,
            "old_paths": [os.fsdecode(p) for p in self.old_paths],
            "items_count": self.items_count,
            "imported_items_count": self.imported_items_count,
            "album_id": self.album_id,
            "archive_extracted": self.archive_extracted,
            "archive_path": os.fsdecode(self.archive_path) if self.archive_path else None,
            "last_tx_id": self.last_tx_id,
            "tx_committed": self.tx_committed,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_json(self) -> str:
        """Serialize the snapshot to a JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def _migrate_v0_to_v1(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Migrate from version 0 (unversioned) to version 1.

        Version 0 had no schema_version field and was missing several
        fields introduced in version 1.
        """
        migrated = dict(data)
        migrated.setdefault("schema_version", 1)
        migrated.setdefault("error_traceback", None)
        migrated.setdefault("replaced_items_count", 0)
        migrated.setdefault("replaced_albums_count", 0)
        migrated.setdefault("old_paths", [])
        migrated.setdefault("archive_extracted", False)
        migrated.setdefault("archive_path", None)
        migrated.setdefault("last_tx_id", None)
        migrated.setdefault("tx_committed", False)
        return migrated

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskSnapshot":
        """Deserialize a snapshot from a dictionary, handling schema migration.

        Raises :class:`SnapshotMigrationError` if the schema version is
        too new to be handled.
        """
        data = dict(data)
        version = data.get("schema_version", 0)

        if version > TASK_SNAPSHOT_SCHEMA_VERSION:
            raise SnapshotMigrationError(
                f"Cannot deserialize TaskSnapshot with schema version {version}; "
                f"this build only supports up to {TASK_SNAPSHOT_SCHEMA_VERSION}."
            )

        if version == 0:
            data = cls._migrate_v0_to_v1(data)

        return cls(
            schema_version=data["schema_version"],
            task_id=data["task_id"],
            toppath=os.fsencode(data["toppath"]) if data.get("toppath") else None,
            paths=[os.fsencode(p) for p in data.get("paths", [])],
            is_album=data.get("is_album", True),
            is_singleton=data.get("is_singleton", False),
            is_sentinel=data.get("is_sentinel", False),
            is_archive=data.get("is_archive", False),
            stage=TaskStage(data["stage"]),
            error_message=data.get("error_message"),
            error_traceback=data.get("error_traceback"),
            choice_flag=data.get("choice_flag"),
            cur_artist=data.get("cur_artist"),
            cur_album=data.get("cur_album"),
            rec=data.get("rec"),
            candidates_count=data.get("candidates_count", 0),
            should_remove_duplicates=data.get("should_remove_duplicates", False),
            should_merge_duplicates=data.get("should_merge_duplicates", False),
            replaced_items_count=data.get("replaced_items_count", 0),
            replaced_albums_count=data.get("replaced_albums_count", 0),
            old_paths=[os.fsencode(p) for p in data.get("old_paths", [])],
            items_count=data.get("items_count", 0),
            imported_items_count=data.get("imported_items_count", 0),
            album_id=data.get("album_id"),
            archive_extracted=data.get("archive_extracted", False),
            archive_path=os.fsencode(data["archive_path"]) if data.get("archive_path") else None,
            last_tx_id=data.get("last_tx_id"),
            tx_committed=data.get("tx_committed", False),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "TaskSnapshot":
        """Deserialize a snapshot from a JSON string."""
        return cls.from_dict(json.loads(json_str))

    def verify_consistency(self, lib: library.Library | None) -> tuple[bool, str]:
        """Verify that this snapshot is consistent with the library database.

        Returns a tuple ``(consistent, message)`` where ``consistent`` is
        True if the snapshot's claims match the database state.

        Consistency checks:
        - If the snapshot claims an ``album_id``, that album must exist in the DB
        - If the snapshot claims ``tx_committed`` is False but stage is past
          ADD, something went wrong and the task should be re-run
        - Items counts should not exceed what's reasonable

        If ``lib`` is None, only the non-DB checks are performed.
        """
        if lib is not None and self.album_id is not None:
            album = lib.get_album(self.album_id)
            if album is None:
                return False, f"Album {self.album_id} referenced by snapshot does not exist in DB"

        if self.stage in {TaskStage.ADD, TaskStage.MANIPULATE_FILES, TaskStage.FINALIZE}:
            if not self.tx_committed and self.last_tx_id is not None:
                return False, f"Stage {self.stage.value} reached but transaction {self.last_tx_id} not marked as committed"

        if self.imported_items_count < 0 or self.items_count < 0:
            return False, "Negative item counts in snapshot"

        if self.imported_items_count > self.items_count:
            return False, "Imported items count exceeds total items count"

        return True, "Snapshot is consistent with database"


@dataclass
class SessionSnapshot:
    """A snapshot of the state of the entire import session.

    Captures session-level state including which paths are being resumed,
    which items/dirs have been merged, and task progress. This enables
    full session recovery after a crash or interruption.

    Schema Versioning
    -----------------
    Like :class:`TaskSnapshot`, this class carries a ``schema_version``
    and supports migration of old serialized data via :meth:`from_dict`.

    Transaction Consistency
    -----------------------
    The session snapshot itself does not participate in database
    transactions, but it references task snapshots that do. Call
    :meth:`verify_consistency` after loading a persisted session to
    ensure all task snapshots are consistent with the database.
    """

    schema_version: int = SESSION_SNAPSHOT_SCHEMA_VERSION

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None

    paths: list[PathBytes] = field(default_factory=list)
    query: str | None = None

    is_resuming: dict[str, bool] = field(default_factory=dict)
    merged_items: list[PathBytes] = field(default_factory=list)
    merged_dirs: list[PathBytes] = field(default_factory=list)

    task_snapshots: dict[str, TaskSnapshot] = field(default_factory=dict)

    total_tasks: int = 0
    completed_tasks: int = 0
    skipped_tasks: int = 0
    errored_tasks: int = 0

    config_snapshot: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Ensure sets are stored as lists for JSON serializability."""
        self._sync_collections()

    def _sync_collections(self) -> None:
        """Ensure internal consistency between set and list representations."""
        # is_resuming: convert bytes keys to str for JSON
        if isinstance(self.is_resuming, dict):
            self.is_resuming = {
                os.fsdecode(k) if isinstance(k, bytes) else str(k): v
                for k, v in self.is_resuming.items()
            }
        # merged_items: convert to list for JSON
        if isinstance(self.merged_items, set):
            self.merged_items = list(self.merged_items)
        # merged_dirs: convert to list for JSON
        if isinstance(self.merged_dirs, set):
            self.merged_dirs = list(self.merged_dirs)

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

    def is_resuming_path(self, toppath: bytes | str) -> bool:
        """Check whether a given toppath is being resumed.

        Accepts both bytes and str keys for convenience.
        """
        key = os.fsdecode(toppath) if isinstance(toppath, bytes) else str(toppath)
        return self.is_resuming.get(key, False)

    def set_resuming(self, toppath: bytes | str, value: bool) -> None:
        """Set the resumption flag for a given toppath."""
        key = os.fsdecode(toppath) if isinstance(toppath, bytes) else str(toppath)
        self.is_resuming[key] = value

    def add_merged_items(self, paths: list[PathBytes]) -> None:
        """Add paths to the merged items set (maintaining list for JSON)."""
        path_set = set(self.merged_items)
        path_set.update(paths)
        self.merged_items = list(path_set)

    def add_merged_dirs(self, paths: list[PathBytes]) -> None:
        """Add paths to the merged dirs set (maintaining list for JSON)."""
        path_set = set(self.merged_dirs)
        path_set.update(paths)
        self.merged_dirs = list(path_set)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the session snapshot to a JSON-compatible dictionary."""
        self._sync_collections()
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "paths": [os.fsdecode(p) for p in self.paths],
            "query": self.query,
            "is_resuming": self.is_resuming,
            "merged_items": [os.fsdecode(p) for p in self.merged_items],
            "merged_dirs": [os.fsdecode(p) for p in self.merged_dirs],
            "task_snapshots": {
                tid: snap.to_dict() for tid, snap in self.task_snapshots.items()
            },
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "skipped_tasks": self.skipped_tasks,
            "errored_tasks": self.errored_tasks,
            "config_snapshot": self.config_snapshot,
        }

    def to_json(self) -> str:
        """Serialize the session snapshot to a JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def _migrate_v0_to_v1(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Migrate from version 0 (unversioned) to version 1."""
        migrated = dict(data)
        migrated.setdefault("schema_version", 1)
        migrated.setdefault("query", None)
        migrated.setdefault("config_snapshot", {})
        return migrated

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionSnapshot":
        """Deserialize a session snapshot, handling schema migration."""
        data = dict(data)
        version = data.get("schema_version", 0)

        if version > SESSION_SNAPSHOT_SCHEMA_VERSION:
            raise SnapshotMigrationError(
                f"Cannot deserialize SessionSnapshot with schema version {version}; "
                f"this build only supports up to {SESSION_SNAPSHOT_SCHEMA_VERSION}."
            )

        if version == 0:
            data = cls._migrate_v0_to_v1(data)

        task_snapshots = {}
        for tid, task_data in data.get("task_snapshots", {}).items():
            try:
                task_snapshots[tid] = TaskSnapshot.from_dict(task_data)
            except SnapshotMigrationError as e:
                log.warning("Skipping corrupted task snapshot {}: {}", tid, e)

        return cls(
            schema_version=data["schema_version"],
            session_id=data["session_id"],
            start_time=data.get("start_time", time.time()),
            end_time=data.get("end_time"),
            paths=[os.fsencode(p) for p in data.get("paths", [])],
            query=data.get("query"),
            is_resuming=data.get("is_resuming", {}),
            merged_items=[os.fsencode(p) for p in data.get("merged_items", [])],
            merged_dirs=[os.fsencode(p) for p in data.get("merged_dirs", [])],
            task_snapshots=task_snapshots,
            total_tasks=data.get("total_tasks", 0),
            completed_tasks=data.get("completed_tasks", 0),
            skipped_tasks=data.get("skipped_tasks", 0),
            errored_tasks=data.get("errored_tasks", 0),
            config_snapshot=data.get("config_snapshot", {}),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "SessionSnapshot":
        """Deserialize a session snapshot from a JSON string."""
        return cls.from_dict(json.loads(json_str))

    def verify_consistency(self, lib: library.Library) -> tuple[bool, list[str]]:
        """Verify all task snapshots are consistent with the database.

        Returns ``(all_consistent, errors)`` where ``errors`` is a list
        of human-readable inconsistency descriptions.
        """
        errors = []
        for task_id, task_snap in self.task_snapshots.items():
            consistent, msg = task_snap.verify_consistency(lib)
            if not consistent:
                errors.append(f"Task {task_id}: {msg}")
        return (len(errors) == 0, errors)


@dataclass
class FactorySnapshot:
    """A snapshot of the state of an ImportTaskFactory.

    Tracks statistics about tasks generated by a factory, including
    skipped paths and imported counts.

    Schema Versioning
    -----------------
    Like the other snapshot classes, this carries a ``schema_version``
    and supports migration via :meth:`from_dict`.
    """

    schema_version: int = FACTORY_SNAPSHOT_SCHEMA_VERSION

    toppath: PathBytes | None = None
    is_archive: bool = False
    skipped: int = 0
    imported: int = 0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "schema_version": self.schema_version,
            "toppath": os.fsdecode(self.toppath) if self.toppath else None,
            "is_archive": self.is_archive,
            "skipped": self.skipped,
            "imported": self.imported,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def _migrate_v0_to_v1(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Migrate from version 0 to version 1."""
        migrated = dict(data)
        migrated.setdefault("schema_version", 1)
        return migrated

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FactorySnapshot":
        """Deserialize from a dictionary, handling schema migration."""
        data = dict(data)
        version = data.get("schema_version", 0)

        if version > FACTORY_SNAPSHOT_SCHEMA_VERSION:
            raise SnapshotMigrationError(
                f"Cannot deserialize FactorySnapshot with schema version {version}; "
                f"this build only supports up to {FACTORY_SNAPSHOT_SCHEMA_VERSION}."
            )

        if version == 0:
            data = cls._migrate_v0_to_v1(data)

        return cls(
            schema_version=data["schema_version"],
            toppath=os.fsencode(data["toppath"]) if data.get("toppath") else None,
            is_archive=data.get("is_archive", False),
            skipped=data.get("skipped", 0),
            imported=data.get("imported", 0),
            created_at=data.get("created_at", time.time()),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "FactorySnapshot":
        """Deserialize from a JSON string."""
        return cls.from_dict(json.loads(json_str))


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

    def _json_path(self) -> PathBytes:
        """Return the path to the JSON-format state file.

        The JSON file is the primary persistence format starting from
        schema version 1. It sits alongside the pickle file for
        backward compatibility.
        """
        path_str = os.fsdecode(self.path)
        root, _ = os.path.splitext(path_str)
        return os.fsencode(root + ".json")

    def _open(self):
        # Try JSON format first (primary format), fallback to pickle.
        json_path = self._json_path()

        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                    self._deserialize_from_dict(state)
                return
            except (OSError, json.JSONDecodeError, SnapshotMigrationError) as exc:
                log.warning(
                    "JSON state file could not be read ({}), "
                    "falling back to pickle format: {}",
                    os.fsdecode(json_path),
                    exc,
                )

        # Fall back to pickle format.
        try:
            with open(self.path, "rb") as f:
                state = pickle.load(f)
                self._deserialize_from_dict(state)
        except Exception as exc:
            log.debug("state file could not be read: {}", exc)

    def _deserialize_from_dict(self, state: dict[str, Any]) -> None:
        """Deserialize state from a dictionary (either JSON or pickle source).

        Handles both old pickle format (with raw objects) and new JSON
        format (with nested dicts that need to be rehydrated).
        """
        # tagprogress: dict may be in JSON format (str keys/values) or
        # pickle format (bytes keys/values). Convert to bytes format.
        tagprogress_raw = state.get("tagprogress", {})
        self.tagprogress = {}
        for k, v in tagprogress_raw.items():
            key = os.fsencode(k) if isinstance(k, str) else k
            value = [os.fsencode(p) if isinstance(p, str) else p for p in v]
            self.tagprogress[key] = value

        # taghistory may be a list (JSON) or set (pickle). Convert to set of tuples of bytes.
        taghistory_raw = state.get("taghistory", [])
        self.taghistory = set()
        for paths in taghistory_raw:
            converted_paths = tuple(
                os.fsencode(p) if isinstance(p, str) else p
                for p in paths
            )
            self.taghistory.add(converted_paths)

        task_snapshots_raw = state.get("task_snapshots", {})
        self.task_snapshots = {}
        for tid, raw in task_snapshots_raw.items():
            try:
                if isinstance(raw, dict):
                    self.task_snapshots[tid] = TaskSnapshot.from_dict(raw)
                else:
                    self.task_snapshots[tid] = raw
            except SnapshotMigrationError as e:
                log.warning("Skipping corrupted task snapshot {}: {}", tid, e)

        session_raw = state.get("session_snapshot", None)
        if session_raw is not None:
            try:
                if isinstance(session_raw, dict):
                    self.session_snapshot = SessionSnapshot.from_dict(session_raw)
                else:
                    self.session_snapshot = session_raw
            except SnapshotMigrationError as e:
                log.warning("Skipping corrupted session snapshot: {}", e)
                self.session_snapshot = None

        factory_snapshots_raw = state.get("factory_snapshots", {})
        self.factory_snapshots = {}
        for toppath, raw in factory_snapshots_raw.items():
            try:
                key = os.fsencode(toppath) if isinstance(toppath, str) else toppath
                if isinstance(raw, dict):
                    self.factory_snapshots[key] = FactorySnapshot.from_dict(raw)
                else:
                    self.factory_snapshots[key] = raw
            except SnapshotMigrationError as e:
                log.warning("Skipping corrupted factory snapshot: {}", e)

    def _save(self):
        # Save JSON format (primary).
        json_path = self._json_path()
        state_dict = self._serialize_to_dict()
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(state_dict, f, indent=2, sort_keys=True)
        except OSError as exc:
            log.error("JSON state file could not be written: {}", exc)

        # Also save pickle format for backward compatibility.
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
            log.error("pickle state file could not be written: {}", exc)

    def _serialize_to_dict(self) -> dict[str, Any]:
        """Serialize the full state to a JSON-compatible dictionary."""
        return {
            "schema_version": 1,
            "tagprogress": {
                os.fsdecode(k): [os.fsdecode(p) for p in v]
                for k, v in self.tagprogress.items()
            },
            "taghistory": [
                [os.fsdecode(p) for p in paths]
                for paths in self.taghistory
            ],
            "task_snapshots": {
                tid: snap.to_dict() for tid, snap in self.task_snapshots.items()
            },
            "session_snapshot": (
                self.session_snapshot.to_dict()
                if self.session_snapshot
                else None
            ),
            "factory_snapshots": {
                os.fsdecode(toppath): snap.to_dict()
                for toppath, snap in self.factory_snapshots.items()
            },
            "saved_at": time.time(),
        }

    # ---------------------------- Transaction Safety ---------------------------- #

    def save_task_snapshot_after_tx(
        self,
        snapshot: TaskSnapshot,
        tx_id: str,
        committed: bool,
    ) -> None:
        """Persist a task snapshot **after** a database transaction.

        This is the preferred way to save snapshots that relate to database
        modifications. It marks the transaction boundary on the snapshot and
        only persists the snapshot if the transaction was committed.

        Parameters
        ----------
        snapshot:
            The task snapshot to persist.
        tx_id:
            A unique identifier for the database transaction.
        committed:
            Whether the transaction was successfully committed. If False,
            the snapshot is updated with the failure information
            (for debugging) but marked as uncommitted.
        """
        snapshot.mark_tx_boundary(tx_id, committed)
        # Only persist after the transaction has committed. If it was
        # rolled back, we still record the boundary for diagnostics but
        # don't persist to disk yet — the next transaction will
        # overwrite it.
        if committed:
            with self as state:
                state.task_snapshots[snapshot.task_id] = snapshot
        else:
            # Still update in-memory in case someone wants to inspect it.
            self.task_snapshots[snapshot.task_id] = snapshot
            log.debug(
                "Not persisting snapshot {} after rolled-back tx {}",
                snapshot.task_id,
                tx_id,
            )

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

    def get_active_task_snapshots(
        self,
        toppath: PathBytes | None = None,
        lib: library.Library | None = None,
    ) -> list[TaskSnapshot]:
        """Return all task snapshots that are not in a terminal state.

        If `toppath` is given, restrict to tasks under that path.
        If `lib` is provided, each snapshot is verified against the
        database, and inconsistent snapshots are excluded (logged
        with a warning).

        This is the primary entry point for crash recovery: call this
        after loading the state to discover which tasks need to be
        re-run.
        """
        terminal = {TaskStage.COMPLETED, TaskStage.SKIPPED}
        results: list[TaskSnapshot] = []
        for snap in self.task_snapshots.values():
            if snap.stage in terminal:
                continue
            if toppath is not None and snap.toppath != toppath:
                continue
            if lib is not None:
                consistent, msg = snap.verify_consistency(lib)
                if not consistent:
                    log.warning(
                        "Excluding inconsistent task snapshot {} from "
                        "active set: {}",
                        snap.task_id,
                        msg,
                    )
                    continue
            results.append(snap)
        return results

    def verify_all_consistency(
        self,
        lib: library.Library,
    ) -> tuple[bool, list[str]]:
        """Verify all persisted snapshots against the database.

        Returns ``(all_consistent, errors)`` where ``errors`` is a list
        of human-readable inconsistency descriptions.
        """
        errors: list[str] = []

        # Check task snapshots.
        for task_id, task_snap in self.task_snapshots.items():
            consistent, msg = task_snap.verify_consistency(lib)
            if not consistent:
                errors.append(f"Task {task_id}: {msg}")

        # Check session snapshot.
        if self.session_snapshot is not None:
            session_ok, session_errors = self.session_snapshot.verify_consistency(lib)
            if not session_ok:
                errors.extend(session_errors)

        # Tagprogress cross-check: paths referenced in tagprogress should
        # not appear in active task snapshots.
        for task_snap in self.task_snapshots.values():
            if task_snap.toppath in self.tagprogress:
                imported = self.tagprogress[task_snap.toppath]
                for path in task_snap.paths:
                    i = bisect_left(imported, path)
                    if i != len(imported) and imported[i] == path:
                        errors.append(
                            f"Task {task_snap.task_id} path "
                            f"{os.fsdecode(path)} is marked as imported in tagprogress "
                            f"but task is in stage {task_snap.stage.value}"
                        )

        return (len(errors) == 0, errors)

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
