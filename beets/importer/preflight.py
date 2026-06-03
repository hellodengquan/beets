from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from beets import config, library, logging, util
from beets.dbcore.query import PathQuery

if TYPE_CHECKING:
    from collections.abc import Iterable

    from beets.importer.tasks import ImportTask, SingletonImportTask
    from beets.library import Album, Item

log = logging.getLogger("beets")

REQUIRED_ITEM_FIELDS = ("artist", "title")
REQUIRED_ALBUM_FIELDS = ("albumartist", "album")


@dataclass
class PreflightItemResult:
    path: bytes
    missing_tags: list[str]
    is_duplicate: bool
    duplicate_ids: list[int]
    path_conflict: bool


@dataclass
class PreflightTaskResult:
    task: ImportTask | SingletonImportTask
    is_album: bool
    items: list[PreflightItemResult]
    duplicate_albums: list[Album] = field(default_factory=list)
    duplicate_items: list[Item] = field(default_factory=list)

    @property
    def has_duplicates(self) -> bool:
        return bool(self.duplicate_albums or self.duplicate_items) or any(
            item.is_duplicate for item in self.items
        )

    @property
    def has_path_conflicts(self) -> bool:
        return any(item.path_conflict for item in self.items)

    @property
    def has_missing_tags(self) -> bool:
        return any(item.missing_tags for item in self.items)

    @property
    def missing_tag_count(self) -> int:
        return sum(len(item.missing_tags) for item in self.items)

    @property
    def duplicate_count(self) -> int:
        return len(self.duplicate_albums) + len(self.duplicate_items) + sum(
            1 for item in self.items if item.is_duplicate
        )

    @property
    def path_conflict_count(self) -> int:
        return sum(1 for item in self.items if item.path_conflict)


@dataclass
class PreflightSummary:
    total_tasks: int = 0
    total_items: int = 0
    album_tasks: int = 0
    singleton_tasks: int = 0
    tasks_with_duplicates: int = 0
    tasks_with_path_conflicts: int = 0
    tasks_with_missing_tags: int = 0
    total_duplicates: int = 0
    total_path_conflicts: int = 0
    total_missing_tags: int = 0
    will_copy: bool = False
    will_move: bool = False
    will_write: bool = False
    will_delete: bool = False
    task_results: list[PreflightTaskResult] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return any(
            [
                self.tasks_with_duplicates > 0,
                self.tasks_with_path_conflicts > 0,
                self.tasks_with_missing_tags > 0,
            ]
        )

    @property
    def risk_level(self) -> str:
        if not self.has_issues:
            return "low"
        total_issues = (
            self.tasks_with_duplicates
            + self.tasks_with_path_conflicts
            + self.tasks_with_missing_tags
        )
        if total_issues <= self.total_tasks * 0.3:
            return "medium"
        return "high"


def _check_missing_tags(
    item: Item, is_album: bool
) -> list[str]:
    missing = []
    for field in REQUIRED_ITEM_FIELDS:
        if not getattr(item, field, None):
            missing.append(field)
    if is_album:
        for field in REQUIRED_ALBUM_FIELDS:
            if not getattr(item, field, None):
                missing.append(field)
    return missing


def _check_path_conflict(item: Item, lib_dir: bytes) -> bool:
    if not item.path or not lib_dir:
        return False
    try:
        item_path = util.normpath(item.path)
        return item_path == lib_dir or lib_dir in util.ancestry(item_path)
    except Exception:
        return False


def _check_metadata_duplicates(
    task: ImportTask | SingletonImportTask,
    lib,
    is_album: bool,
) -> tuple[list[Album], list[Item]]:
    """Check for duplicates based on metadata without requiring choice_flag."""
    duplicate_albums: list[Album] = []
    duplicate_items: list[Item] = []

    task_items = getattr(task, "items", [])
    if not is_album and hasattr(task, "item"):
        task_items = [task.item]

    if not task_items:
        return duplicate_albums, duplicate_items

    try:
        if is_album:
            likelies, _ = util.get_most_common_tags(task_items)
            info = dict(likelies)
            if not info.get("artist"):
                return duplicate_albums, duplicate_items

            info["albumartist"] = info["artist"]
            tmp_album = library.Album(lib, **info)
            keys: list[str] = config["import"]["duplicate_keys"][
                "album"
            ].as_str_seq()
            dup_query = tmp_album.duplicates_query(keys)

            task_paths = {i.path for i in task_items if i}
            for album in lib.albums(dup_query):
                album_paths = {i.path for i in album.items()}
                if not (album_paths <= task_paths):
                    duplicate_albums.append(album)
        else:
            item = task_items[0]
            info = dict(item)
            tmp_item = library.Item(lib, **info)
            keys: list[str] = config["import"]["duplicate_keys"][
                "item"
            ].as_str_seq()
            dup_query = tmp_item.duplicates_query(keys)

            for other_item in lib.items(dup_query):
                if other_item.path != item.path:
                    duplicate_items.append(other_item)
    except Exception as e:
        log.debug(f"Error checking metadata duplicates: {e}")

    return duplicate_albums, duplicate_items


def run_preflight(
    tasks: Iterable[ImportTask | SingletonImportTask],
    lib,
) -> PreflightSummary:
    summary = PreflightSummary()
    lib_dir = util.normpath(lib.directory)

    iconfig = config["import"]
    summary.will_copy = iconfig["copy"].get(bool)
    summary.will_move = iconfig["move"].get(bool)
    summary.will_write = iconfig["write"].get(bool)
    summary.will_delete = iconfig["delete"].get(bool)

    for task in tasks:
        if getattr(task, "skip", False):
            continue

        is_album = getattr(task, "is_album", True)
        if is_album:
            summary.album_tasks += 1
        else:
            summary.singleton_tasks += 1
        summary.total_tasks += 1

        task_items = getattr(task, "items", [])
        if not is_album and hasattr(task, "item"):
            task_items = [task.item]

        item_results = []
        for item in task_items:
            summary.total_items += 1
            missing_tags = _check_missing_tags(item, is_album)
            path_conflict = _check_path_conflict(item, lib_dir)

            dup_items = list(lib.items(query=PathQuery("path", item.path)))
            is_duplicate = len(dup_items) > 0
            duplicate_ids = [dup.id for dup in dup_items if dup.id]

            item_results.append(
                PreflightItemResult(
                    path=item.path,
                    missing_tags=missing_tags,
                    is_duplicate=is_duplicate,
                    duplicate_ids=duplicate_ids,
                    path_conflict=path_conflict,
                )
            )

        task_result = PreflightTaskResult(
            task=task,
            is_album=is_album,
            items=item_results,
        )

        dup_albums, dup_items = _check_metadata_duplicates(task, lib, is_album)
        task_result.duplicate_albums = dup_albums
        task_result.duplicate_items = dup_items

        if task_result.has_duplicates:
            summary.tasks_with_duplicates += 1
            summary.total_duplicates += task_result.duplicate_count
        if task_result.has_path_conflicts:
            summary.tasks_with_path_conflicts += 1
            summary.total_path_conflicts += task_result.path_conflict_count
        if task_result.has_missing_tags:
            summary.tasks_with_missing_tags += 1
            summary.total_missing_tags += task_result.missing_tag_count

        summary.task_results.append(task_result)

    return summary
