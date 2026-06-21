from __future__ import annotations

import os
import pickle
import tempfile

import pytest

from beets.importer.state import ImportState, _normpath


@pytest.fixture
def state_file(tmp_path):
    return str(tmp_path / "state.pickle")


@pytest.fixture
def state(state_file):
    return ImportState(path=os.fsencode(state_file))


class TestNormPath:
    @pytest.mark.parametrize(
        "path",
        [
            b"/Music/Album/Track.mp3",
            b"/music/album/track.mp3",
            b"/MUSIC/ALBUM/TRACK.MP3",
        ],
    )
    def test_normpath_is_idempotent(self, path):
        assert _normpath(_normpath(path)) == _normpath(path)

    def test_normpath_case_insensitive_on_ci_fs(self):
        upper = _normpath(b"/Music/Album/Track.mp3")
        lower = _normpath(b"/music/album/track.mp3")
        if os.path.normcase("A") != "A":
            assert upper == lower
        else:
            assert upper != lower


class TestImportStateProgress:
    def test_progress_add_and_has_element(self, state, state_file):
        toppath = b"/import"
        path_a = b"/import/album/track1.mp3"
        path_b = b"/import/album/track2.mp3"

        state.progress_add(toppath, path_a)
        state.progress_add(toppath, path_b)

        state2 = ImportState(path=os.fsencode(state_file))
        assert state2.progress_has_element(toppath, path_a)
        assert state2.progress_has_element(toppath, path_b)
        assert not state2.progress_has_element(toppath, b"/import/album/track3.mp3")

    def test_progress_has(self, state):
        toppath = b"/import"
        assert not state.progress_has(toppath)
        state.progress_add(toppath, b"/import/album/track1.mp3")
        assert state.progress_has(toppath)

    def test_progress_reset(self, state, state_file):
        toppath = b"/import"
        state.progress_add(toppath, b"/import/album/track1.mp3")
        assert state.progress_has(toppath)

        state.progress_reset(toppath)

        state2 = ImportState(path=os.fsencode(state_file))
        assert not state2.progress_has(toppath)

    def test_progress_case_insensitive_toppath(self, state, state_file):
        toppath_upper = b"/Music/Import"
        toppath_lower = b"/music/import"
        path = b"/Music/Import/album/track.mp3"

        state.progress_add(toppath_upper, path)

        state2 = ImportState(path=os.fsencode(state_file))
        if os.path.normcase("A") != "A":
            assert state2.progress_has(toppath_lower)
            assert state2.progress_has_element(toppath_lower, path)
            assert state2.progress_has_element(toppath_lower, b"/music/import/album/track.mp3")

    def test_progress_case_insensitive_paths(self, state, state_file):
        toppath = b"/import"
        path_upper = b"/import/Album/Track.mp3"
        path_lower = b"/import/album/track.mp3"

        state.progress_add(toppath, path_upper)

        state2 = ImportState(path=os.fsencode(state_file))
        if os.path.normcase("A") != "A":
            assert state2.progress_has_element(toppath, path_lower)

    def test_progress_survives_pickle_roundtrip(self, state_file):
        toppath = b"/import"
        paths = [b"/import/a.mp3", b"/import/b.mp3"]

        state = ImportState(path=os.fsencode(state_file))
        state.progress_add(toppath, *paths)

        state2 = ImportState(path=os.fsencode(state_file))
        for p in paths:
            assert state2.progress_has_element(toppath, p)

    def test_progress_add_maintains_sorted_order(self, state, state_file):
        toppath = b"/import"
        state.progress_add(toppath, b"/import/c.mp3")
        state.progress_add(toppath, b"/import/a.mp3")
        state.progress_add(toppath, b"/import/b.mp3")

        state2 = ImportState(path=os.fsencode(state_file))
        norm_top = _normpath(toppath)
        stored = state2.tagprogress[norm_top]
        assert stored == sorted(stored)


class TestImportStateHistory:
    def test_history_add_and_check(self, state, state_file):
        paths = [b"/import/album1", b"/import/album2"]
        state.history_add(paths)

        state2 = ImportState(path=os.fsencode(state_file))
        norm_paths = tuple(_normpath(p) for p in paths)
        assert norm_paths in state2.taghistory

    def test_history_case_insensitive(self, state, state_file):
        paths_upper = [b"/Music/Album1", b"/Music/Album2"]
        state.history_add(paths_upper)

        state2 = ImportState(path=os.fsencode(state_file))
        paths_lower = tuple(_normpath(p) for p in [b"/music/album1", b"/music/album2"])
        if os.path.normcase("A") != "A":
            assert paths_lower in state2.taghistory


class TestImportStateResume:
    def test_resume_skips_already_processed(self, state_file):
        toppath = b"/import"
        path1 = b"/import/album1/track.mp3"
        path2 = b"/import/album2/track.mp3"

        state = ImportState(path=os.fsencode(state_file))
        state.progress_add(toppath, path1)

        state2 = ImportState(path=os.fsencode(state_file))
        assert state2.progress_has_element(toppath, path1)
        assert not state2.progress_has_element(toppath, path2)

    def test_resume_with_case_variant_skips(self, state_file):
        toppath = b"/Music/Import"
        path = b"/Music/Import/Album/Track.mp3"

        state = ImportState(path=os.fsencode(state_file))
        state.progress_add(toppath, path)

        state2 = ImportState(path=os.fsencode(state_file))
        if os.path.normcase("A") != "A":
            variant_toppath = b"/music/import"
            variant_path = b"/music/import/album/track.mp3"
            assert state2.progress_has_element(variant_toppath, variant_path)

    def test_interrupted_resume_tracks_partial_progress(self, state_file):
        toppath = b"/import"
        paths_album1 = [b"/import/album1/track1.mp3", b"/import/album1/track2.mp3"]
        paths_album2 = [b"/import/album2/track1.mp3"]

        state = ImportState(path=os.fsencode(state_file))
        state.progress_add(toppath, *paths_album1)

        state2 = ImportState(path=os.fsencode(state_file))
        for p in paths_album1:
            assert state2.progress_has_element(toppath, p)
        for p in paths_album2:
            assert not state2.progress_has_element(toppath, p)

        state2.progress_add(toppath, *paths_album2)

        state3 = ImportState(path=os.fsencode(state_file))
        for p in paths_album1 + paths_album2:
            assert state3.progress_has_element(toppath, p)

    def test_invalid_state_file_is_handled_gracefully(self, state_file):
        with open(state_file, "wb") as f:
            f.write(b"not a valid pickle")
        state = ImportState(path=os.fsencode(state_file))
        assert state.tagprogress == {}
        assert state.taghistory == set()

    def test_empty_state_file_is_handled(self, state_file):
        state = ImportState(path=os.fsencode(state_file))
        assert state.tagprogress == {}
        assert state.taghistory == set()


class TestImportStateResumeEdgeCases:
    """Edge case tests for interrupted import resume functionality.

    These tests cover scenarios where:
    - Import is interrupted multiple times
    - File paths have case variations between sessions
    - Some files in an album are processed, others are not
    - Already imported files should be skipped correctly
    """

    def test_resume_after_multiple_interruptions(self, state_file):
        """Test resume after multiple import interruptions.

        Simulates a scenario where an import is interrupted twice
        and verify that all previously processed files are correctly
        identified as already imported.
        """
        toppath = b"/music/collection"
        batch1 = [
            b"/music/collection/album1/track01.mp3",
            b"/music/collection/album1/track02.mp3",
        ]
        batch2 = [
            b"/music/collection/album1/track03.mp3",
            b"/music/collection/album2/track01.mp3",
        ]
        batch3 = [
            b"/music/collection/album2/track02.mp3",
            b"/music/collection/album2/track03.mp3",
        ]

        state1 = ImportState(path=os.fsencode(state_file))
        state1.progress_add(toppath, *batch1)

        state2 = ImportState(path=os.fsencode(state_file))
        for p in batch1:
            assert state2.progress_has_element(toppath, p)
        state2.progress_add(toppath, *batch2)

        state3 = ImportState(path=os.fsencode(state_file))
        for p in batch1 + batch2:
            assert state3.progress_has_element(toppath, p)
        state3.progress_add(toppath, *batch3)

        state4 = ImportState(path=os.fsencode(state_file))
        for p in batch1 + batch2 + batch3:
            assert state4.progress_has_element(toppath, p)

    def test_resume_with_mixed_case_paths(self, state_file):
        """Test that path case differences don't cause file reprocessing.

        When resuming an import, files that were processed with a different
        case variant (e.g., "/Music/Album" vs "/music/album") should still
        be recognized as already processed on case-insensitive filesystems.
        """
        toppath_upper = b"/Music/MyCollection"
        toppath_lower = b"/music/mycollection"

        paths_upper = [
            b"/Music/MyCollection/Rock/Album1/Track01.MP3",
            b"/Music/MyCollection/Rock/Album1/Track02.MP3",
        ]
        paths_lower = [
            b"/music/mycollection/rock/album1/track01.mp3",
            b"/music/mycollection/rock/album1/track02.mp3",
        ]

        state = ImportState(path=os.fsencode(state_file))
        state.progress_add(toppath_upper, *paths_upper)

        state2 = ImportState(path=os.fsencode(state_file))
        if os.path.normcase("A") != "A":
            for toppath in [toppath_upper, toppath_lower]:
                for path in paths_upper + paths_lower:
                    assert state2.progress_has_element(toppath, path), (
                        f"Path {path} should be recognized as processed "
                        f"under toppath {toppath}"
                    )
        else:
            for path in paths_upper:
                assert state2.progress_has_element(toppath_upper, path)
            for path in paths_lower:
                assert not state2.progress_has_element(toppath_lower, path)

    def test_resume_partial_album_import(self, state_file):
        """Test that partial album imports are correctly handled on resume.

        When an import is interrupted in the middle of an album,
        only the already processed tracks should be skipped on resume.
        Unprocessed tracks from the same album should still be imported.
        """
        toppath = b"/import/artist_album"
        all_tracks = [
            b"/import/artist_album/track01.mp3",
            b"/import/artist_album/track02.mp3",
            b"/import/artist_album/track03.mp3",
            b"/import/artist_album/track04.mp3",
            b"/import/artist_album/track05.mp3",
        ]
        processed_tracks = all_tracks[:3]
        unprocessed_tracks = all_tracks[3:]

        state = ImportState(path=os.fsencode(state_file))
        state.progress_add(toppath, *processed_tracks)

        state2 = ImportState(path=os.fsencode(state_file))
        assert state2.progress_has(toppath)
        for p in processed_tracks:
            assert state2.progress_has_element(toppath, p)
        for p in unprocessed_tracks:
            assert not state2.progress_has_element(toppath, p)

    def test_already_imported_skips_case_variants(self, state_file, monkeypatch):
        """Test ImportState correctly handles path case variants.

        This tests that progress_has_element correctly identifies
        files even when path case differs from the original import,
        which is the core logic used by ImportSession.already_imported.
        """
        toppath = b"/Music/Import"
        path = b"/Music/Import/Album/Track.mp3"
        path_variant = b"/music/import/album/track.mp3"

        state = ImportState(path=os.fsencode(state_file))
        state.progress_add(toppath, path)

        state2 = ImportState(path=os.fsencode(state_file))
        assert state2.progress_has_element(toppath, path)
        if os.path.normcase("A") != "A":
            assert state2.progress_has_element(toppath, path_variant)
            assert state2.progress_has_element(b"/music/import", path)
            assert state2.progress_has_element(b"/music/import", path_variant)
        else:
            assert not state2.progress_has_element(toppath, path_variant)

    def test_already_imported_logic_with_resume(self, state_file):
        """Test the core logic of ImportSession.already_imported.

        Tests the logic that determines if files should be skipped
        during a resumed import, without needing to instantiate the
        full ImportSession with all its dependencies.
        """
        from beets.importer.state import _normpath

        toppath = b"/import"
        paths = [
            b"/import/album1/track1.mp3",
            b"/import/album1/track2.mp3",
            b"/import/album2/track1.mp3",
        ]
        processed_paths = paths[:2]
        unprocessed_path = paths[2]

        state = ImportState(path=os.fsencode(state_file))
        state.progress_add(toppath, *processed_paths)

        is_resuming = True
        state2 = ImportState(path=os.fsencode(state_file))

        for p in processed_paths:
            assert is_resuming and state2.progress_has_element(toppath, p)

        assert not state2.progress_has_element(toppath, unprocessed_path)

        assert _normpath(toppath) in state2.tagprogress
        norm_processed = [_normpath(p) for p in processed_paths]
        stored = state2.tagprogress[_normpath(toppath)]
        for p in norm_processed:
            assert p in stored

    def test_progress_add_duplicate_paths(self, state, state_file):
        """Test that adding duplicate paths doesn't corrupt progress state.

        Adding the same path multiple times should maintain the sorted
        order of the progress list, and progress_has_element should
        correctly identify the path as processed.
        """
        toppath = b"/import"
        path = b"/import/album/track.mp3"

        state.progress_add(toppath, path)
        state.progress_add(toppath, path)
        state.progress_add(toppath, path)

        state2 = ImportState(path=os.fsencode(state_file))
        assert state2.progress_has_element(toppath, path)
        norm_top = _normpath(toppath)
        stored = state2.tagprogress[norm_top]
        assert stored == sorted(stored)
        assert all(p == _normpath(path) for p in stored)

    def test_progress_add_duplicate_paths_does_not_affect_has_element(self, state, state_file):
        """Test that duplicate paths don't break progress_has_element.

        Even if duplicate paths are stored, progress_has_element should
        correctly return True for existing paths.
        """
        toppath = b"/import"
        paths = [
            b"/import/album/track1.mp3",
            b"/import/album/track2.mp3",
        ]

        for _ in range(3):
            state.progress_add(toppath, *paths)

        state2 = ImportState(path=os.fsencode(state_file))
        for path in paths:
            assert state2.progress_has_element(toppath, path)
        assert not state2.progress_has_element(toppath, b"/import/album/track3.mp3")

    def test_resume_without_progress_returns_false(self, state_file):
        """Test that is_resuming returns False when there's no progress.

        When a toppath has no recorded progress, the import should
        not be treated as a resumable session.
        """
        from beets.importer.session import ImportSession
        from beets.library import Library

        toppath = b"/new/import"
        lib = Library(":memory:")
        session = ImportSession(lib, None, [toppath], None)
        session.want_resume = True

        assert not ImportState(path=os.fsencode(state_file)).progress_has(toppath)
        assert not session.is_resuming(toppath)

    def test_incremental_import_case_insensitive_history(self, state_file):
        """Test that incremental import history handles case variants.

        The taghistory used for incremental imports should also be
        case-insensitive on appropriate filesystems.
        """
        paths_upper = [b"/Music/Album1", b"/Music/Album2"]
        paths_lower = [b"/music/album1", b"/music/album2"]

        state = ImportState(path=os.fsencode(state_file))
        state.history_add(paths_upper)

        state2 = ImportState(path=os.fsencode(state_file))
        norm_paths_upper = tuple(_normpath(p) for p in paths_upper)
        norm_paths_lower = tuple(_normpath(p) for p in paths_lower)

        assert norm_paths_upper in state2.taghistory
        if os.path.normcase("A") != "A":
            assert norm_paths_upper == norm_paths_lower
            assert norm_paths_lower in state2.taghistory
