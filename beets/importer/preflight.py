from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from beets import config, library, logging, util
from beets.dbcore.query import PathQuery
from beets.util import displayable_path
from beets.util.color import colorize

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
        return lib_dir in util.ancestry(item_path)
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


def format_preflight_summary(summary: PreflightSummary) -> str:
    lines = []

    risk_colors = {
        "low": "green",
        "medium": "yellow",
        "high": "red",
    }
    risk_color = risk_colors.get(summary.risk_level, "text_default")
    risk_text = colorize(risk_color, summary.risk_level.upper())

    lines.append("")
    lines.append(colorize("text_highlight", "=" * 60))
    lines.append(
        colorize(
            "text_highlight",
            f"  导入预检摘要 - 风险等级: {risk_text}",
        )
    )
    lines.append(colorize("text_highlight", "=" * 60))
    lines.append("")

    lines.append(colorize("text_highlight_minor", "  统计信息:"))
    lines.append(f"    总任务数: {summary.total_tasks}")
    lines.append(f"      专辑导入: {summary.album_tasks}")
    lines.append(f"      单曲导入: {summary.singleton_tasks}")
    lines.append(f"    总曲目数: {summary.total_items}")
    lines.append("")

    lines.append(colorize("text_highlight_minor", "  预计执行动作:"))
    actions = []
    if summary.will_copy:
        actions.append(colorize("cyan", "复制文件"))
    if summary.will_move:
        actions.append(colorize("yellow", "移动文件"))
    if summary.will_write:
        actions.append(colorize("cyan", "写入标签"))
    if summary.will_delete:
        actions.append(colorize("red", "删除源文件"))
    if not actions:
        actions.append(colorize("text_default", "仅添加到数据库"))
    lines.append(f"    {', '.join(actions)}")
    lines.append("")

    if summary.has_issues:
        lines.append(colorize("text_highlight_minor", "  检测到的问题:"))

        if summary.tasks_with_duplicates > 0:
            dup_color = "yellow" if summary.tasks_with_duplicates > 0 else "text_default"
            lines.append(
                colorize(
                    dup_color,
                    f"    重复项: {summary.tasks_with_duplicates} 个任务, "
                    f"共 {summary.total_duplicates} 个重复",
                )
            )

        if summary.tasks_with_path_conflicts > 0:
            pc_color = "red" if summary.tasks_with_path_conflicts > 0 else "text_default"
            lines.append(
                colorize(
                    pc_color,
                    f"    路径冲突: {summary.tasks_with_path_conflicts} 个任务, "
                    f"共 {summary.total_path_conflicts} 个冲突",
                )
            )

        if summary.tasks_with_missing_tags > 0:
            mt_color = "yellow" if summary.tasks_with_missing_tags > 0 else "text_default"
            lines.append(
                colorize(
                    mt_color,
                    f"    缺失标签: {summary.tasks_with_missing_tags} 个任务, "
                    f"共 {summary.total_missing_tags} 个缺失",
                )
            )
        lines.append("")

        lines.append(colorize("text_highlight_minor", "  详细信息:"))
        for i, result in enumerate(summary.task_results, 1):
            if not (
                result.has_duplicates
                or result.has_path_conflicts
                or result.has_missing_tags
            ):
                continue

            task_type = "专辑" if result.is_album else "单曲"
            path_str = displayable_path(result.task.paths[0])
            if len(path_str) > 50:
                path_str = "..." + path_str[-47:]

            issues = []
            if result.has_duplicates:
                issues.append(f"重复({result.duplicate_count})")
            if result.has_path_conflicts:
                issues.append(f"路径冲突({result.path_conflict_count})")
            if result.has_missing_tags:
                issues.append(f"缺标签({result.missing_tag_count})")

            issue_str = ", ".join(issues)
            lines.append(
                f"    {i:2d}. [{task_type}] {path_str}"
            )
            lines.append(
                f"        {colorize('yellow', issue_str)}"
            )

            for item in result.items:
                item_issues = []
                if item.is_duplicate:
                    item_issues.append("重复")
                if item.path_conflict:
                    item_issues.append("路径冲突")
                if item.missing_tags:
                    item_issues.append(f"缺: {', '.join(item.missing_tags)}")
                if item_issues:
                    item_path = displayable_path(item.path)
                    if len(item_path) > 45:
                        item_path = "..." + item_path[-42:]
                    lines.append(
                        f"          - {item_path}: "
                        f"{colorize('yellow', ', '.join(item_issues))}"
                    )
        lines.append("")

    lines.append(colorize("text_highlight", "=" * 60))
    lines.append("")

    return "\n".join(lines)
