"""Unit tests for the ImportAuditSummary data class and related functionality.

Covers edge cases and extreme scenarios:
- Empty / zero-count (no import tasks) state
- Per-category counting: applied, as-is, skipped
- Pure duplicate handling three-branch scenarios
- Pure manual-review flagging
- Task description generation (album vs. singleton)
- Public API exposure and contract validation
"""

from __future__ import annotations

import os
import tempfile

import pytest

from beets import importer, library
from beets.autotag.hooks import AlbumInfo, AlbumMatch, TrackInfo
from beets.autotag.match import distance
from beets.importer import (
    Action,
    ImportAuditSummary,
    ImportSession,
    ImportTask,
    SentinelImportTask,
    SingletonImportTask,
)
from beets.test import _common


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_match(items, artist="Tag Artist", album="Tag Album"):
    """Build a minimal AlbumMatch for testing APPLY actions."""
    info = AlbumInfo(
        album=album,
        album_id=f"album-id-{album}",
        artist=artist,
        artist_id=f"artist-id-{artist}",
        tracks=[
            TrackInfo(
                title=f"Tag Track {i+1}",
                index=i + 1,
                track_id=f"track-id-{i+1}",
            )
            for i in range(len(items))
        ],
    )
    pairs = list(zip(items, info.tracks))
    dist = distance(items, info, pairs)
    return AlbumMatch(dist, info, dict(pairs))


def _album_task(items, toppath=b"/test", paths=None):
    paths = paths or [toppath + b"/album"]
    return ImportTask(toppath=toppath, paths=paths, items=list(items))


def _singleton_task(item, toppath=b"/test"):
    return SingletonImportTask(toppath=toppath, item=item)


class _NullSession(ImportSession):
    """Concrete ImportSession subclass that does nothing interactive."""

    def should_resume(self, path):
        return False

    def choose_match(self, task):  # pragma: no cover - never reached
        return Action.ASIS

    def resolve_duplicate(self, task, found_duplicates):  # pragma: no cover
        pass

    def choose_item(self, task):  # pragma: no cover - never reached
        return Action.ASIS


# ---------------------------------------------------------------------------
# 1. Empty / zero-count state — the "no tasks processed" scenario
# ---------------------------------------------------------------------------


class TestEmptyState:
    """Assert invariants of a freshly-constructed / zero-usage summary."""

    def test_defaults_all_zero(self):
        s = ImportAuditSummary()
        assert s.applied == 0
        assert s.asis == 0
        assert s.skipped == 0
        assert s.duplicate_replace == 0
        assert s.duplicate_keep == 0
        assert s.duplicate_skip == 0
        assert s.needs_review == 0
        assert s.total_tasks == 0

    def test_defaults_empty_detail_lists(self):
        s = ImportAuditSummary()
        assert s.applied_details == []
        assert s.asis_details == []
        assert s.skipped_details == []
        assert s.duplicate_replace_details == []
        assert s.duplicate_keep_details == []
        assert s.duplicate_skip_details == []
        assert s.needs_review_details == []

    def test_session_starts_with_empty_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = tmp.encode()
            lib = library.Library(":memory:", os.path.join(tmp, b"lib"))
            session = _NullSession(lib, None, [b"/music"], None)
            assert isinstance(session.audit_summary, ImportAuditSummary)
            assert session.audit_summary.total_tasks == 0


# ---------------------------------------------------------------------------
# 2. Per-category counting — applied / as-is / skipped
# ---------------------------------------------------------------------------


class TestCategoryCounts:
    """Verify each non-duplicate action increments exactly one bucket."""

    def test_applied_counts_once_per_task(self):
        s = ImportAuditSummary()
        item = _common.item()
        match = _make_match([item])

        t1 = _album_task([item])
        t1.set_choice(match)
        s.record_choice(t1)

        t2 = _album_task([item], paths=[b"/other"])
        t2.set_choice(match)
        s.record_choice(t2)

        assert s.applied == 2
        assert s.total_tasks == 2
        # Others stay at zero.
        assert s.asis == 0
        assert s.skipped == 0

    def test_applied_detail_contains_path_and_desc(self):
        s = ImportAuditSummary()
        item = _common.item()
        match = _make_match([item], artist="Radiohead", album="Kid A")

        t = _album_task([item], toppath=b"/music", paths=[b"/music/radiohead_kid_a"])
        t.set_choice(match)
        s.record_choice(t)

        assert len(s.applied_details) == 1
        path_display, desc = s.applied_details[0]
        assert "radiohead_kid_a" in path_display
        assert "Radiohead" in desc
        assert "Kid A" in desc

    def test_asis_counts_and_details(self):
        s = ImportAuditSummary()
        item = _common.item()

        t = _singleton_task(item)
        t.set_choice(Action.ASIS)
        s.record_choice(t)

        assert s.asis == 1
        assert s.total_tasks == 1
        assert s.applied == 0
        assert len(s.asis_details) == 1

    def test_skip_counts_and_details(self):
        s = ImportAuditSummary()
        item = _common.item()

        t = _album_task([item], paths=[b"/skip/me"])
        t.set_choice(Action.SKIP)
        s.record_choice(t)

        assert s.skipped == 1
        assert s.total_tasks == 1
        path_display, _ = s.skipped_details[0]
        assert "skip" in path_display

    def test_mixed_non_duplicate_actions_sum_correctly(self):
        s = ImportAuditSummary()
        item = _common.item()
        match = _make_match([item])

        for _ in range(3):
            t = _album_task([item])
            t.set_choice(match)
            s.record_choice(t)
        for _ in range(2):
            t = _album_task([item])
            t.set_choice(Action.ASIS)
            s.record_choice(t)
        for _ in range(5):
            t = _album_task([item])
            t.set_choice(Action.SKIP)
            s.record_choice(t)

        assert s.applied == 3
        assert s.asis == 2
        assert s.skipped == 5
        assert s.total_tasks == 10


# ---------------------------------------------------------------------------
# 3. Pure duplicate-handling — each of the three branches in isolation
# ---------------------------------------------------------------------------


class TestDuplicateBranches:
    """Exercise the three duplicate-resolution outcomes without noise.

    duplicate=True flips the record_choice method into its secondary-path
    logic where only three buckets matter: replace / keep / skip.
    """

    def test_duplicate_replace_increments_only_replace(self):
        s = ImportAuditSummary()
        item = _common.item()
        match = _make_match([item])

        t = _album_task([item], paths=[b"/dup/replace"])
        t.set_choice(match)
        t.should_remove_duplicates = True
        s.record_choice(t, duplicate=True)

        assert s.duplicate_replace == 1
        # The main branches must NOT be touched on the duplicate path.
        assert s.applied == 0
        assert s.asis == 0
        assert s.skipped == 0
        # And the other duplicate branches stay empty.
        assert s.duplicate_keep == 0
        assert s.duplicate_skip == 0
        assert s.total_tasks == 1

        path_display, _ = s.duplicate_replace_details[0]
        assert "replace" in path_display

    def test_duplicate_keep_via_asis(self):
        s = ImportAuditSummary()
        item = _common.item()

        t = _album_task([item], paths=[b"/dup/keep"])
        t.set_choice(Action.ASIS)
        s.record_choice(t, duplicate=True)

        assert s.duplicate_keep == 1
        assert s.duplicate_replace == 0
        assert s.duplicate_skip == 0
        assert s.asis == 0
        assert s.total_tasks == 1

    def test_duplicate_keep_via_apply(self):
        """APPLY on a duplicate path is also 'keep both'."""
        s = ImportAuditSummary()
        item = _common.item()
        match = _make_match([item])

        t = _album_task([item])
        t.set_choice(match)
        s.record_choice(t, duplicate=True)

        assert s.duplicate_keep == 1
        assert s.duplicate_replace == 0
        assert s.duplicate_skip == 0

    def test_duplicate_skip_branch(self):
        s = ImportAuditSummary()
        item = _common.item()

        t = _album_task([item], paths=[b"/dup/skip_new"])
        t.set_choice(Action.SKIP)
        s.record_choice(t, duplicate=True)

        assert s.duplicate_skip == 1
        assert s.duplicate_keep == 0
        assert s.duplicate_replace == 0
        assert s.skipped == 0
        assert s.total_tasks == 1

    def test_all_three_duplicate_branches_coexist(self):
        s = ImportAuditSummary()
        item = _common.item()
        match = _make_match([item])

        # Replace
        t1 = _album_task([item])
        t1.set_choice(match)
        t1.should_remove_duplicates = True
        s.record_choice(t1, duplicate=True)

        # Keep (via ASIS)
        t2 = _album_task([item])
        t2.set_choice(Action.ASIS)
        s.record_choice(t2, duplicate=True)

        # Skip
        t3 = _album_task([item])
        t3.set_choice(Action.SKIP)
        s.record_choice(t3, duplicate=True)

        assert s.duplicate_replace == 1
        assert s.duplicate_keep == 1
        assert s.duplicate_skip == 1
        assert s.total_tasks == 3


# ---------------------------------------------------------------------------
# 4. Pure manual-review / needs_review flagging
# ---------------------------------------------------------------------------


class TestNeedsReview:
    """The needs_review flag should cut across all decision buckets."""

    def test_needs_review_with_applied_action(self):
        s = ImportAuditSummary()
        item = _common.item()
        match = _make_match([item])

        t = _album_task([item])
        t.set_choice(match)
        s.record_choice(t, needs_review=True)

        assert s.needs_review == 1
        assert s.applied == 1
        assert len(s.needs_review_details) == 1

    def test_needs_review_with_asis_action(self):
        s = ImportAuditSummary()
        item = _common.item()

        t = _album_task([item])
        t.set_choice(Action.ASIS)
        s.record_choice(t, needs_review=True)

        assert s.needs_review == 1
        assert s.asis == 1

    def test_needs_review_with_skip_action(self):
        s = ImportAuditSummary()
        item = _common.item()

        t = _album_task([item])
        t.set_choice(Action.SKIP)
        s.record_choice(t, needs_review=True)

        assert s.needs_review == 1
        assert s.skipped == 1

    def test_needs_review_with_duplicate_replace(self):
        s = ImportAuditSummary()
        item = _common.item()
        match = _make_match([item])

        t = _album_task([item])
        t.set_choice(match)
        t.should_remove_duplicates = True
        s.record_choice(t, duplicate=True, needs_review=True)

        assert s.needs_review == 1
        assert s.duplicate_replace == 1

    def test_all_review_all_actions(self):
        """A fully-interactive session marks every task as reviewed."""
        s = ImportAuditSummary()
        item = _common.item()
        match = _make_match([item])

        for i in range(4):
            t = _album_task([item], paths=[f"/t{i}".encode()])
            t.set_choice([match, Action.ASIS, Action.SKIP, match][i])
            if i == 3:
                t.should_remove_duplicates = True
                s.record_choice(t, duplicate=True, needs_review=True)
            else:
                s.record_choice(t, needs_review=True)

        assert s.needs_review == 4
        assert s.total_tasks == 4


# ---------------------------------------------------------------------------
# 5. Sentinel tasks must be completely ignored
# ---------------------------------------------------------------------------


class TestSentinelIgnored:
    """Sentinel tasks are progress markers and never count."""

    def test_sentinel_does_not_affect_any_counter(self):
        s = ImportAuditSummary()
        sentinel = SentinelImportTask(b"/toppath", None)

        s.record_choice(sentinel)

        assert s.total_tasks == 0
        assert s.applied == s.asis == s.skipped == 0
        for detail_list in (
            s.applied_details,
            s.asis_details,
            s.skipped_details,
            s.needs_review_details,
        ):
            assert detail_list == []


# ---------------------------------------------------------------------------
# 6. Description generation — album and singleton code paths
# ---------------------------------------------------------------------------


class TestTaskDescription:
    """Coverage for the static helper _task_description."""

    def test_album_apply_uses_match_metadata(self):
        item = _common.item()
        match = _make_match([item], artist="Aphex Twin", album="Selected Ambient Works")

        t = _album_task([item])
        t.set_choice(match)

        desc = ImportAuditSummary._task_description(t)
        assert "Aphex Twin" in desc
        assert "Selected Ambient Works" in desc

    def test_album_asis_uses_item_metadata(self):
        # albumartist is the preferred fallback key for album tasks.
        item = _common.item(artist="Björk", albumartist="Björk", album="Vespertine")

        t = _album_task([item])
        t.set_choice(Action.ASIS)

        desc = ImportAuditSummary._task_description(t)
        assert "Björk" in desc
        assert "Vespertine" in desc

    def test_singleton_asis_uses_item_artist_title(self):
        item = _common.item(artist="Boards of Canada", title="Roygbiv")

        t = _singleton_task(item)
        t.set_choice(Action.ASIS)

        desc = ImportAuditSummary._task_description(t)
        assert "Boards of Canada" in desc
        assert "Roygbiv" in desc

    def test_album_no_choice_falls_back_to_item_metadata(self):
        """When choice_flag is None (e.g. before user_query stage),
        the description should not crash and should use sensible fallbacks.
        """
        item = _common.item(
            artist="Fallback Artist",
            albumartist="Fallback Artist",
            album="Fallback Album",
        )

        t = _album_task([item])
        # choice_flag remains unset

        desc = ImportAuditSummary._task_description(t)
        assert "Fallback Artist" in desc
        assert "Fallback Album" in desc

    def test_missing_metadata_reports_unknown(self):
        # Construct without any string fields at all.
        bare_item = library.Item()
        bare_item.path = b"/foo/bar.flac"

        t = _singleton_task(bare_item)
        t.set_choice(Action.ASIS)

        desc = ImportAuditSummary._task_description(t)
        # "Unknown" tokens should appear since artist / title are absent.
        assert "Unknown" in desc

    def test_exception_during_description_caught(self):
        """Malformed tasks should not break the audit path."""
        from unittest.mock import patch

        item = _common.item()
        t = _album_task([item])
        t.set_choice(Action.ASIS)

        # Simulate an error coming out of chosen_info().
        with patch.object(t, "chosen_info", side_effect=RuntimeError("boom")):
            desc = ImportAuditSummary._task_description(t)

        assert desc == "N/A"


# ---------------------------------------------------------------------------
# 7. Public API contract — ImportAuditSummary is exported
# ---------------------------------------------------------------------------


class TestPublicAPI:
    """Make sure the data class and fields are visible where plugins expect."""

    def test_imported_from_importer_module(self):
        from beets.importer import ImportAuditSummary as S1
        from beets.importer.__init__ import ImportAuditSummary as S2

        assert S1 is S2 is ImportAuditSummary

    def test_all_contract_fields_exist(self):
        """Plugins depend on these names; treat any rename as breaking change."""
        required = [
            "applied",
            "applied_details",
            "asis",
            "asis_details",
            "skipped",
            "skipped_details",
            "duplicate_replace",
            "duplicate_replace_details",
            "duplicate_keep",
            "duplicate_keep_details",
            "duplicate_skip",
            "duplicate_skip_details",
            "needs_review",
            "needs_review_details",
            "total_tasks",
            "record_choice",
        ]
        s = ImportAuditSummary()
        for name in required:
            assert hasattr(s, name), f"missing API field/method: {name}"
