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

"""Tests for the preflight import check functionality."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock

from beets import config
from beets.importer import Action
from beets.importer import tasks as importer_tasks
from beets.importer.preflight import (
    REQUIRED_ALBUM_FIELDS,
    REQUIRED_ITEM_FIELDS,
    PreflightItemResult,
    PreflightSummary,
    PreflightTaskResult,
    _check_missing_tags,
    _check_path_conflict,
    run_preflight,
)
from beets.test.helper import ImportTestCase
from beets.util import bytestring_path


class TestCheckMissingTags(unittest.TestCase):
    """Tests for the _check_missing_tags function."""

    def setUp(self):
        self.lib = MagicMock()

    def _create_item(self, **kwargs):
        item = MagicMock()
        for key, value in kwargs.items():
            setattr(item, key, value)
        return item

    def test_item_all_tags_present(self):
        item = self._create_item(artist="Artist", title="Title")
        missing = _check_missing_tags(item, is_album=False)
        self.assertEqual(missing, [])

    def test_item_missing_artist(self):
        item = self._create_item(artist="", title="Title")
        missing = _check_missing_tags(item, is_album=False)
        self.assertEqual(missing, ["artist"])

    def test_item_missing_title(self):
        item = self._create_item(artist="Artist", title="")
        missing = _check_missing_tags(item, is_album=False)
        self.assertEqual(missing, ["title"])

    def test_item_missing_both(self):
        item = self._create_item(artist="", title="")
        missing = _check_missing_tags(item, is_album=False)
        self.assertEqual(set(missing), {"artist", "title"})

    def test_album_all_tags_present(self):
        item = self._create_item(
            artist="Artist",
            title="Title",
            albumartist="Album Artist",
            album="Album",
        )
        missing = _check_missing_tags(item, is_album=True)
        self.assertEqual(missing, [])

    def test_album_missing_albumartist(self):
        item = self._create_item(
            artist="Artist",
            title="Title",
            albumartist="",
            album="Album",
        )
        missing = _check_missing_tags(item, is_album=True)
        self.assertEqual(missing, ["albumartist"])

    def test_album_missing_album(self):
        item = self._create_item(
            artist="Artist",
            title="Title",
            albumartist="Album Artist",
            album="",
        )
        missing = _check_missing_tags(item, is_album=True)
        self.assertEqual(missing, ["album"])

    def test_album_missing_multiple(self):
        item = self._create_item(
            artist="",
            title="",
            albumartist="",
            album="",
        )
        missing = _check_missing_tags(item, is_album=True)
        self.assertEqual(
            set(missing),
            {"artist", "title", "albumartist", "album"},
        )

    def test_item_none_values(self):
        item = self._create_item(artist=None, title=None)
        missing = _check_missing_tags(item, is_album=False)
        self.assertEqual(set(missing), {"artist", "title"})

    def test_required_fields_constants(self):
        self.assertEqual(REQUIRED_ITEM_FIELDS, ("artist", "title"))
        self.assertEqual(REQUIRED_ALBUM_FIELDS, ("albumartist", "album"))


class TestCheckPathConflict(unittest.TestCase):
    """Tests for the _check_path_conflict function."""

    def setUp(self):
        self.lib_dir = bytestring_path(os.path.abspath("/music/library"))

    def test_path_outside_lib_dir(self):
        item = MagicMock()
        item.path = bytestring_path(os.path.abspath("/downloads/music/song.mp3"))
        result = _check_path_conflict(item, self.lib_dir)
        self.assertFalse(result)

    def test_path_inside_lib_dir(self):
        item = MagicMock()
        item.path = bytestring_path(
            os.path.abspath("/music/library/artist/album/song.mp3")
        )
        result = _check_path_conflict(item, self.lib_dir)
        self.assertTrue(result)

    def test_path_exactly_lib_dir(self):
        item = MagicMock()
        item.path = self.lib_dir
        result = _check_path_conflict(item, self.lib_dir)
        self.assertTrue(result)

    def test_empty_path(self):
        item = MagicMock()
        item.path = b""
        result = _check_path_conflict(item, self.lib_dir)
        self.assertFalse(result)

    def test_none_path(self):
        item = MagicMock()
        item.path = None
        result = _check_path_conflict(item, self.lib_dir)
        self.assertFalse(result)

    def test_empty_lib_dir(self):
        item = MagicMock()
        item.path = bytestring_path("/some/path.mp3")
        result = _check_path_conflict(item, b"")
        self.assertFalse(result)

    def test_nested_path_conflict(self):
        item = MagicMock()
        item.path = bytestring_path(
            "/music/library/a/b/c/d/e/song.mp3"
        )
        result = _check_path_conflict(item, self.lib_dir)
        self.assertTrue(result)


class TestPreflightSummaryRiskLevel(unittest.TestCase):
    """Tests for PreflightSummary.risk_level property."""

    def test_no_issues_low_risk(self):
        summary = PreflightSummary(
            total_tasks=10,
            tasks_with_duplicates=0,
            tasks_with_path_conflicts=0,
            tasks_with_missing_tags=0,
        )
        self.assertEqual(summary.risk_level, "low")

    def test_zero_tasks_low_risk(self):
        summary = PreflightSummary(
            total_tasks=0,
            tasks_with_duplicates=0,
            tasks_with_path_conflicts=0,
            tasks_with_missing_tags=0,
        )
        self.assertEqual(summary.risk_level, "low")

    def test_below_30_percent_medium_risk(self):
        summary = PreflightSummary(
            total_tasks=10,
            tasks_with_duplicates=2,
            tasks_with_path_conflicts=0,
            tasks_with_missing_tags=0,
        )
        self.assertEqual(summary.risk_level, "medium")

    def test_exactly_30_percent_medium_risk(self):
        summary = PreflightSummary(
            total_tasks=10,
            tasks_with_duplicates=3,
            tasks_with_path_conflicts=0,
            tasks_with_missing_tags=0,
        )
        self.assertEqual(summary.risk_level, "medium")

    def test_above_30_percent_high_risk(self):
        summary = PreflightSummary(
            total_tasks=10,
            tasks_with_duplicates=4,
            tasks_with_path_conflicts=0,
            tasks_with_missing_tags=0,
        )
        self.assertEqual(summary.risk_level, "high")

    def test_all_tasks_issues_high_risk(self):
        summary = PreflightSummary(
            total_tasks=5,
            tasks_with_duplicates=2,
            tasks_with_path_conflicts=1,
            tasks_with_missing_tags=2,
        )
        self.assertEqual(summary.risk_level, "high")

    def test_mixed_issues_medium(self):
        summary = PreflightSummary(
            total_tasks=20,
            tasks_with_duplicates=2,
            tasks_with_path_conflicts=1,
            tasks_with_missing_tags=2,
        )
        self.assertEqual(summary.risk_level, "medium")

    def test_single_task_with_issue_high_risk(self):
        summary = PreflightSummary(
            total_tasks=1,
            tasks_with_duplicates=0,
            tasks_with_path_conflicts=1,
            tasks_with_missing_tags=0,
        )
        self.assertEqual(summary.risk_level, "high")

    def test_has_issues_property(self):
        summary = PreflightSummary(
            total_tasks=5,
            tasks_with_duplicates=1,
            tasks_with_path_conflicts=0,
            tasks_with_missing_tags=0,
        )
        self.assertTrue(summary.has_issues)

        summary2 = PreflightSummary(
            total_tasks=5,
            tasks_with_duplicates=0,
            tasks_with_path_conflicts=0,
            tasks_with_missing_tags=0,
        )
        self.assertFalse(summary2.has_issues)


class TestPreflightTaskResult(unittest.TestCase):
    """Tests for PreflightTaskResult properties."""

    def setUp(self):
        self.task = MagicMock()
        self.task.paths = [b"/path/to/album"]

    def test_has_duplicates_true(self):
        item_result = PreflightItemResult(
            path=b"/path/to/file.mp3",
            missing_tags=[],
            is_duplicate=True,
            duplicate_ids=[1],
            path_conflict=False,
        )
        result = PreflightTaskResult(
            task=self.task,
            is_album=True,
            items=[item_result],
        )
        self.assertTrue(result.has_duplicates)
        self.assertEqual(result.duplicate_count, 1)

    def test_has_duplicates_false(self):
        item_result = PreflightItemResult(
            path=b"/path/to/file.mp3",
            missing_tags=[],
            is_duplicate=False,
            duplicate_ids=[],
            path_conflict=False,
        )
        result = PreflightTaskResult(
            task=self.task,
            is_album=True,
            items=[item_result],
        )
        self.assertFalse(result.has_duplicates)

    def test_has_path_conflicts(self):
        item_result = PreflightItemResult(
            path=b"/path/to/file.mp3",
            missing_tags=[],
            is_duplicate=False,
            duplicate_ids=[],
            path_conflict=True,
        )
        result = PreflightTaskResult(
            task=self.task,
            is_album=True,
            items=[item_result],
        )
        self.assertTrue(result.has_path_conflicts)
        self.assertEqual(result.path_conflict_count, 1)

    def test_has_missing_tags(self):
        item_result = PreflightItemResult(
            path=b"/path/to/file.mp3",
            missing_tags=["artist"],
            is_duplicate=False,
            duplicate_ids=[],
            path_conflict=False,
        )
        result = PreflightTaskResult(
            task=self.task,
            is_album=True,
            items=[item_result],
        )
        self.assertTrue(result.has_missing_tags)
        self.assertEqual(result.missing_tag_count, 1)

    def test_multiple_items_counts(self):
        items = [
            PreflightItemResult(
                path=f"/path/to/file{i}.mp3".encode(),
                missing_tags=["artist"] if i == 0 else [],
                is_duplicate=(i == 1),
                duplicate_ids=[1] if i == 1 else [],
                path_conflict=(i == 2),
            )
            for i in range(3)
        ]
        result = PreflightTaskResult(
            task=self.task,
            is_album=True,
            items=items,
        )
        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(result.path_conflict_count, 1)
        self.assertEqual(result.missing_tag_count, 1)
        self.assertTrue(result.has_duplicates)
        self.assertTrue(result.has_path_conflicts)
        self.assertTrue(result.has_missing_tags)


class TestRunPreflight(ImportTestCase):
    """Integration tests for run_preflight function."""

    db_on_disk = True

    def setUp(self):
        super().setUp()
        self.prepare_album_for_import(2)
        self.media_paths = [
            bytestring_path(str(m.path)) for m in self.import_media
        ]
        self.album_path = bytestring_path(
            os.path.dirname(str(self.import_media[0].path))
        )

    def _create_item_for_path(self, path, **kwargs):
        """Create a library.Item for a given path with metadata."""
        from beets.library import Item

        item = Item(self.lib, **kwargs)
        if isinstance(path, bytes):
            item.path = path
        else:
            item.path = bytestring_path(str(path))
        return item

    def test_empty_task_list(self):
        summary = run_preflight([], self.lib)
        self.assertEqual(summary.total_tasks, 0)
        self.assertEqual(summary.total_items, 0)
        self.assertEqual(summary.risk_level, "low")
        self.assertFalse(summary.has_issues)

    def test_single_album_task_no_issues(self):
        items = [
            self._create_item_for_path(
                p,
                artist="Artist",
                title=f"Title {i}",
                albumartist="Album Artist",
                album="Album",
            )
            for i, p in enumerate(self.media_paths)
        ]
        task = importer_tasks.ImportTask(
            self.album_path,
            [self.album_path],
            items,
        )

        summary = run_preflight([task], self.lib)

        self.assertEqual(summary.total_tasks, 1)
        self.assertEqual(summary.album_tasks, 1)
        self.assertEqual(summary.singleton_tasks, 0)
        self.assertEqual(summary.total_items, 2)
        self.assertFalse(summary.has_issues)
        self.assertEqual(summary.risk_level, "low")

    def test_single_singleton_task_no_issues(self):
        item = self._create_item_for_path(
            self.media_paths[0],
            artist="Artist",
            title="Title",
        )
        task = importer_tasks.SingletonImportTask(
            None,
            item,
        )

        summary = run_preflight([task], self.lib)

        self.assertEqual(summary.total_tasks, 1)
        self.assertEqual(summary.album_tasks, 0)
        self.assertEqual(summary.singleton_tasks, 1)
        self.assertEqual(summary.total_items, 1)
        self.assertFalse(summary.has_issues)

    def test_mixed_album_and_singleton(self):
        album_items = [
            self._create_item_for_path(
                p,
                artist="Artist",
                title=f"Title {i}",
                albumartist="Album Artist",
                album="Album",
            )
            for i, p in enumerate(self.media_paths)
        ]
        album_task = importer_tasks.ImportTask(
            self.album_path,
            [self.album_path],
            album_items,
        )

        singleton_item = self._create_item_for_path(
            self.media_paths[0],
            artist="Artist2",
            title="Title2",
        )
        singleton_task = importer_tasks.SingletonImportTask(
            None,
            singleton_item,
        )

        summary = run_preflight([album_task, singleton_task], self.lib)

        self.assertEqual(summary.total_tasks, 2)
        self.assertEqual(summary.album_tasks, 1)
        self.assertEqual(summary.singleton_tasks, 1)
        self.assertEqual(summary.total_items, 3)

    def test_path_conflict_detection(self):
        item = self.add_item(
            artist="Test Artist",
            title="Test Title",
            album="Test Album",
        )

        import_item = self._create_item_for_path(
            item.path,
            artist="Test Artist",
            title="Test Title",
            album="Test Album",
        )
        task = importer_tasks.SingletonImportTask(
            None,
            import_item,
        )

        summary = run_preflight([task], self.lib)

        self.assertEqual(summary.tasks_with_path_conflicts, 1)
        self.assertEqual(summary.total_path_conflicts, 1)

    def test_duplicate_detection_path(self):
        item = self.add_item(
            artist="Test Artist",
            title="Test Title",
        )

        import_item = self._create_item_for_path(
            item.path,
            artist="Test Artist",
            title="Test Title",
        )
        task = importer_tasks.SingletonImportTask(
            None,
            import_item,
        )

        summary = run_preflight([task], self.lib)

        self.assertTrue(summary.tasks_with_duplicates >= 1)
        self.assertTrue(summary.total_duplicates >= 1)

    def test_multiple_issues_same_task(self):
        lib_item = self.add_item(
            artist="",
            title="",
            album="Test Album",
        )

        import_item = self._create_item_for_path(
            lib_item.path,
            artist="",
            title="",
            album="Test Album",
        )
        task = importer_tasks.SingletonImportTask(
            None,
            import_item,
        )

        summary = run_preflight([task], self.lib)

        self.assertTrue(summary.tasks_with_duplicates >= 1)
        self.assertTrue(summary.tasks_with_path_conflicts >= 1)
        self.assertTrue(summary.tasks_with_missing_tags >= 1)
        self.assertEqual(summary.risk_level, "high")

    def test_planned_actions_detection(self):
        config["import"]["copy"] = True
        config["import"]["move"] = False
        config["import"]["write"] = True
        config["import"]["delete"] = False

        item = self._create_item_for_path(
            self.media_paths[0],
            artist="Artist",
            title="Title",
        )
        task = importer_tasks.SingletonImportTask(
            None,
            item,
        )

        summary = run_preflight([task], self.lib)

        self.assertTrue(summary.will_copy)
        self.assertFalse(summary.will_move)
        self.assertTrue(summary.will_write)
        self.assertFalse(summary.will_delete)

    def test_planned_actions_move_and_delete(self):
        config["import"]["copy"] = False
        config["import"]["move"] = True
        config["import"]["write"] = False
        config["import"]["delete"] = True

        item = self._create_item_for_path(
            self.media_paths[0],
            artist="Artist",
            title="Title",
        )
        task = importer_tasks.SingletonImportTask(
            None,
            item,
        )

        summary = run_preflight([task], self.lib)

        self.assertFalse(summary.will_copy)
        self.assertTrue(summary.will_move)
        self.assertFalse(summary.will_write)
        self.assertTrue(summary.will_delete)

    def test_skip_task_filtered_out(self):
        items = [
            self._create_item_for_path(
                p,
                artist="Artist",
                title=f"Title {i}",
                albumartist="Album Artist",
                album="Album",
            )
            for i, p in enumerate(self.media_paths)
        ]
        task = importer_tasks.ImportTask(
            self.album_path,
            [self.album_path],
            items,
        )
        task.choice_flag = Action.SKIP

        summary = run_preflight([task], self.lib)

        self.assertEqual(summary.total_tasks, 0)
        self.assertEqual(summary.total_items, 0)

    def test_task_results_structure(self):
        item = self._create_item_for_path(
            self.media_paths[0],
            artist="Artist",
            title="Title",
        )
        task = importer_tasks.SingletonImportTask(
            None,
            item,
        )

        summary = run_preflight([task], self.lib)

        self.assertEqual(len(summary.task_results), 1)
        result = summary.task_results[0]
        self.assertFalse(result.is_album)
        self.assertEqual(len(result.items), 1)
        self.assertIsInstance(result.items[0], PreflightItemResult)
        self.assertEqual(result.items[0].path, item.path)

    def test_medium_risk_multiple_tasks(self):
        tasks = []
        for i in range(8):
            item = self._create_item_for_path(
                self.media_paths[0],
                artist=f"Artist {i}",
                title=f"Title {i}",
            )
            task = importer_tasks.SingletonImportTask(
                None,
                item,
            )
            tasks.append(task)

        tasks_with_issues = 2
        for i in range(tasks_with_issues):
            lib_item = self.add_item(
                artist=f"Lib Artist {i}",
                title=f"Lib Title {i}",
            )
            import_item = self._create_item_for_path(
                self.media_paths[0],
                artist=f"Lib Artist {i}",
                title=f"Lib Title {i}",
            )
            task = importer_tasks.SingletonImportTask(
                None,
                import_item,
            )
            tasks.append(task)

        summary = run_preflight(tasks, self.lib)

        self.assertEqual(summary.risk_level, "medium")


def suite():
    return unittest.TestLoader().loadTestsFromName(__name__)


if __name__ == "__main__":
    unittest.main(defaultTest="suite")
