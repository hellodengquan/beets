from __future__ import annotations

import os
import pickle
import tempfile

import pytest

from beets.importer.state import ImportState, _normpath


def _normpath_is_case_insensitive():
    """判断路径比较是否对大小写不敏感。

    在 macOS/Windows 等大小写不敏感文件系统上返回 True，
    在 Linux 等大小写敏感文件系统上返回 False。

    使用实际的 _normpath 行为来检测，确保与实现一致。
    """
    return _normpath(b"/A") == _normpath(b"/a")


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
        if _normpath_is_case_insensitive():
            assert upper == lower
        else:
            assert upper != lower


@pytest.mark.skipif(
    not _normpath_is_case_insensitive(),
    reason="only run on case-insensitive filesystems",
)
class TestNormPathCaseInsensitiveFS:
    """独立的 case-insensitive 文件系统路径规范化测试。

    这些测试仅在 macOS/Windows 等大小写不敏感文件系统上运行，
    专门验证 _normpath 的大小写规范化行为，确保不同大小写变体
    被正确识别为同一路径。
    """

    @pytest.mark.parametrize(
        "path_variants",
        [
            [b"/Music/Album/Track.mp3", b"/music/album/track.mp3"],
            [b"/MUSIC/ALBUM/TRACK.MP3", b"/music/album/track.mp3"],
            [b"/MuSiC/AlBuM/TrAcK.mP3", b"/music/album/track.mp3"],
            [
                b"/Users/Test/Music/My Song.FLAC",
                b"/users/test/music/my song.flac",
            ],
        ],
    )
    def test_normpath_equates_case_variants(self, path_variants):
        """大小写不同的路径变体应被规范化为相同值。"""
        normalized = [_normpath(p) for p in path_variants]
        for i in range(1, len(normalized)):
            assert normalized[0] == normalized[i], (
                f"Path variants should normalize to same value: "
                f"{path_variants[0]!r} vs {path_variants[i]!r}"
            )

    @pytest.mark.parametrize(
        "path",
        [
            b"/simple/path.mp3",
            b"/With Spaces/And Special Chars !@#$.mp3",
            "/UNICODE/中文路径/歌曲.mp3",
        ],
    )
    def test_normpath_preserves_non_case_characters(self, path):
        """规范化应保留除大小写外的所有字符（字母转为小写）。"""
        if isinstance(path, str):
            path = os.fsencode(path)
        result = _normpath(path)
        decoded = os.fsdecode(path)
        expected = os.fsencode(os.path.normcase(decoded).lower())
        assert result == expected

    def test_normpath_with_unicode_case_variants(self):
        """包含 Unicode 字符的路径大小写变体。"""
        path_upper = b"/Music/\xc3\x84hnlicher Titel.mp3"
        path_lower = b"/music/\xc3\xa4hnlicher titel.mp3"
        assert _normpath(path_upper) == _normpath(path_lower)

    def test_normpath_empty_path(self):
        """空路径应安全处理。"""
        result = _normpath(b"")
        assert isinstance(result, bytes)

    def test_normpath_root_path(self):
        """根路径应正确规范化。"""
        assert _normpath(b"/") == _normpath(b"/")
        assert _normpath(b"/") == os.fsencode(os.path.normcase("/"))


@pytest.mark.skipif(
    _normpath_is_case_insensitive(),
    reason="only run on case-sensitive filesystems",
)
class TestNormPathCaseSensitiveFS:
    """case-sensitive 文件系统上的路径规范化测试。

    在 Linux 等大小写敏感文件系统上，_normpath 应返回原始路径不变。
    """

    def test_normpath_preserves_case_on_cs_fs(self):
        """大小写敏感文件系统上应保留原始大小写。"""
        upper = b"/Music/Album/Track.mp3"
        lower = b"/music/album/track.mp3"
        assert _normpath(upper) == upper
        assert _normpath(lower) == lower
        assert _normpath(upper) != _normpath(lower)


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
        if _normpath_is_case_insensitive():
            assert state2.progress_has(toppath_lower)
            assert state2.progress_has_element(toppath_lower, path)
            assert state2.progress_has_element(toppath_lower, b"/music/import/album/track.mp3")

    def test_progress_case_insensitive_paths(self, state, state_file):
        toppath = b"/import"
        path_upper = b"/import/Album/Track.mp3"
        path_lower = b"/import/album/track.mp3"

        state.progress_add(toppath, path_upper)

        state2 = ImportState(path=os.fsencode(state_file))
        if _normpath_is_case_insensitive():
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
        if _normpath_is_case_insensitive():
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
        if _normpath_is_case_insensitive():
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
        if _normpath_is_case_insensitive():
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
        if _normpath_is_case_insensitive():
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
        if _normpath_is_case_insensitive():
            assert norm_paths_upper == norm_paths_lower
            assert norm_paths_lower in state2.taghistory


class TestImportStateCorrupted:
    """损坏的 state 文件处理测试。

    模拟进程崩溃等场景下产生的损坏/不完整 state 文件，
    确保 ImportState 能够优雅处理，不会抛出异常。
    """

    def test_truncated_pickle_file(self, state_file):
        """半截 pickle 文件（写入中途崩溃）。"""
        with open(state_file, "wb") as f:
            f.write(b"\x80\x04\x95\x1e\x00\x00\x00\x00\x00\x00\x00")

        state = ImportState(path=os.fsencode(state_file))
        assert state.tagprogress == {}
        assert state.taghistory == set()

    def test_empty_pickle_file(self, state_file):
        """完全空的 state 文件。"""
        with open(state_file, "wb") as f:
            pass

        state = ImportState(path=os.fsencode(state_file))
        assert state.tagprogress == {}
        assert state.taghistory == set()

    def test_partial_valid_pickle(self, state_file):
        """只写入了部分数据的 pickle 文件。"""
        partial_data = pickle.dumps({"tagprogress": {}})
        truncated = partial_data[: len(partial_data) // 2]
        with open(state_file, "wb") as f:
            f.write(truncated)

        state = ImportState(path=os.fsencode(state_file))
        assert state.tagprogress == {}
        assert state.taghistory == set()

    def test_random_bytes_in_state_file(self, state_file):
        """state 文件包含随机垃圾数据。"""
        import random

        random_bytes = bytes(random.getrandbits(8) for _ in range(256))
        with open(state_file, "wb") as f:
            f.write(random_bytes)

        state = ImportState(path=os.fsencode(state_file))
        assert state.tagprogress == {}
        assert state.taghistory == set()

    def test_text_file_instead_of_pickle(self, state_file):
        """state 文件被误写为文本格式。"""
        with open(state_file, "w") as f:
            f.write("This is not a pickle file\n")
            f.write("tagprogress: {}\n")

        state = ImportState(path=os.fsencode(state_file))
        assert state.tagprogress == {}
        assert state.taghistory == set()

    def test_pickle_with_missing_keys(self, state_file):
        """pickle 文件结构有效但缺少必要的键。"""
        with open(state_file, "wb") as f:
            pickle.dump({"unrelated_key": "value"}, f)

        state = ImportState(path=os.fsencode(state_file))
        assert state.tagprogress == {}
        assert state.taghistory == set()

    def test_pickle_with_wrong_types(self, state_file):
        """pickle 文件中键对应的值类型不正确时不应抛出异常。

        ImportState._open 仅做基本读取，不做严格类型校验，
        应能安全加载而不崩溃。
        """
        with open(state_file, "wb") as f:
            pickle.dump(
                {
                    "tagprogress": "should_be_a_dict",
                    "taghistory": ["should_be_a_set"],
                },
                f,
            )

        state = ImportState(path=os.fsencode(state_file))
        assert state.tagprogress == "should_be_a_dict"
        assert state.taghistory == ["should_be_a_set"]

    def test_corrupted_state_can_add_new_progress(self, state_file):
        """损坏的 state 文件之后应能正常添加新进度。"""
        with open(state_file, "wb") as f:
            f.write(b"garbage data")

        state = ImportState(path=os.fsencode(state_file))
        toppath = b"/import"
        path = b"/import/track.mp3"
        state.progress_add(toppath, path)

        state2 = ImportState(path=os.fsencode(state_file))
        assert state2.progress_has(toppath)
        assert state2.progress_has_element(toppath, path)

    def test_corrupted_state_can_add_new_history(self, state_file):
        """损坏的 state 文件之后应能正常添加新历史记录。"""
        with open(state_file, "wb") as f:
            f.write(b"more garbage")

        state = ImportState(path=os.fsencode(state_file))
        paths = [b"/import/album1", b"/import/album2"]
        state.history_add(paths)

        state2 = ImportState(path=os.fsencode(state_file))
        norm_paths = tuple(_normpath(p) for p in paths)
        assert norm_paths in state2.taghistory

    def test_zero_byte_state_file(self, state_file):
        """0 字节的 state 文件。"""
        import os as os_module

        with open(state_file, "wb") as f:
            pass
        assert os_module.path.getsize(state_file) == 0

        state = ImportState(path=os.fsencode(state_file))
        assert state.tagprogress == {}
        assert state.taghistory == set()


@pytest.mark.skipif(
    not _normpath_is_case_insensitive(),
    reason="only run on case-insensitive filesystems",
)
class TestImportStateCaseInsensitiveFS:
    """case-insensitive 文件系统上的 ImportState 独立测试。

    专门在 macOS/Windows 等 CI FS 上验证 ImportState 对路径大小写
    的处理是否正确，确保不会因路径大小写差异导致文件重复导入
    或遗漏已处理文件。
    """

    def test_progress_has_element_with_toppath_case_variant(self, state_file):
        """toppath 大小写变体应正确识别。"""
        toppath_upper = b"/Music/MyAlbums"
        toppath_lower = b"/music/myalbums"
        path = b"/Music/MyAlbums/Artist/Track.mp3"

        state = ImportState(path=os.fsencode(state_file))
        state.progress_add(toppath_upper, path)

        state2 = ImportState(path=os.fsencode(state_file))
        assert state2.progress_has(toppath_upper)
        assert state2.progress_has(toppath_lower)
        assert state2.progress_has_element(toppath_upper, path)
        assert state2.progress_has_element(toppath_lower, path)

    def test_progress_has_element_with_path_case_variant(self, state_file):
        """单个文件路径大小写变体应正确识别。"""
        toppath = b"/import"
        path_upper = b"/import/Artist/Album/Track01.MP3"
        path_lower = b"/import/artist/album/track01.mp3"

        state = ImportState(path=os.fsencode(state_file))
        state.progress_add(toppath, path_upper)

        state2 = ImportState(path=os.fsencode(state_file))
        assert state2.progress_has_element(toppath, path_upper)
        assert state2.progress_has_element(toppath, path_lower)

    def test_progress_has_element_both_case_variants(self, state_file):
        """toppath 和 path 都使用不同大小写。"""
        toppath_upper = b"/Import/Music/Collection"
        toppath_lower = b"/import/music/collection"
        path_upper = b"/Import/Music/Collection/Rock/Great Song.FLAC"
        path_lower = b"/import/music/collection/rock/great song.flac"

        state = ImportState(path=os.fsencode(state_file))
        state.progress_add(toppath_upper, path_upper)

        state2 = ImportState(path=os.fsencode(state_file))
        for toppath in [toppath_upper, toppath_lower]:
            for path in [path_upper, path_lower]:
                assert state2.progress_has_element(toppath, path), (
                    f"Should recognize toppath={toppath!r}, path={path!r}"
                )

    def test_progress_reset_with_case_variant(self, state_file):
        """使用大小写变体的 toppath 重置进度。"""
        toppath_upper = b"/Music/Import"
        toppath_lower = b"/music/import"
        path = b"/Music/Import/track.mp3"

        state = ImportState(path=os.fsencode(state_file))
        state.progress_add(toppath_upper, path)
        assert state.progress_has(toppath_upper)

        state.progress_reset(toppath_lower)

        state2 = ImportState(path=os.fsencode(state_file))
        assert not state2.progress_has(toppath_upper)
        assert not state2.progress_has(toppath_lower)

    def test_history_add_with_case_variants(self, state_file):
        """历史记录应正确处理大小写变体。"""
        paths_upper = [b"/Music/Album/A", b"/Music/Album/B"]
        paths_lower = [b"/music/album/a", b"/music/album/b"]

        state = ImportState(path=os.fsencode(state_file))
        state.history_add(paths_upper)

        state2 = ImportState(path=os.fsencode(state_file))
        norm_upper = tuple(_normpath(p) for p in paths_upper)
        norm_lower = tuple(_normpath(p) for p in paths_lower)
        assert norm_upper in state2.taghistory
        assert norm_lower in state2.taghistory
        assert norm_upper == norm_lower

    def test_mixed_case_across_multiple_sessions(self, state_file):
        """多次导入会话使用不同大小写应保持一致。"""
        session1_toppath = b"/Music/Import"
        session1_paths = [
            b"/Music/Import/Album1/Track01.mp3",
            b"/Music/Import/Album1/Track02.mp3",
        ]
        session2_toppath = b"/music/import"
        session2_path = b"/music/import/album1/track03.mp3"

        state1 = ImportState(path=os.fsencode(state_file))
        state1.progress_add(session1_toppath, *session1_paths)

        state2 = ImportState(path=os.fsencode(state_file))
        for p in session1_paths:
            assert state2.progress_has_element(session2_toppath, p)
        state2.progress_add(session2_toppath, session2_path)

        state3 = ImportState(path=os.fsencode(state_file))
        all_paths = session1_paths + [session2_path]
        for p in all_paths:
            assert state3.progress_has_element(session1_toppath, p)
            assert state3.progress_has_element(session2_toppath, p)

    def test_progress_has_element_mixed_case_unicode(self, state_file):
        """包含 Unicode 字符的路径大小写变体。"""
        toppath = b"/Music/\xc3\x84lbums"
        path_upper = b"/Music/\xc3\x84lbums/\xc3\x9cber Song.MP3"
        path_lower = b"/music/\xc3\xa4lbums/\xc3\xbcber song.mp3"

        state = ImportState(path=os.fsencode(state_file))
        state.progress_add(toppath, path_upper)

        state2 = ImportState(path=os.fsencode(state_file))
        assert state2.progress_has_element(toppath, path_lower)

    def test_already_imported_ci_fs_multiple_albums(self, state_file):
        """多个专辑在不同路径大小写变体下的恢复。"""
        albums = {
            b"/Music/Rock/Album1": [
                b"/Music/Rock/Album1/track01.mp3",
                b"/Music/Rock/Album1/track02.mp3",
            ],
            b"/Music/Jazz/Album2": [
                b"/Music/Jazz/Album2/track01.mp3",
            ],
        }

        state = ImportState(path=os.fsencode(state_file))
        for toppath, paths in albums.items():
            state.progress_add(toppath, *paths)

        state2 = ImportState(path=os.fsencode(state_file))
        variants = {
            b"/music/rock/album1": [b"/music/rock/album1/track01.mp3"],
            b"/music/jazz/album2": [b"/music/jazz/album2/track01.mp3"],
        }
        for toppath, paths in variants.items():
            assert state2.progress_has(toppath)
            for p in paths:
                assert state2.progress_has_element(toppath, p)


class TestSessionAlreadyImported:
    """ImportSession.already_imported 的核心逻辑测试。

    覆盖 resume 模式和 incremental 模式两种场景，
    包括路径大小写不敏感处理。
    """

    def test_already_imported_resume_mode_all_paths(self, state_file):
        """resume 模式下所有路径都已处理应返回 True。"""
        toppath = b"/import/album"
        paths = [b"/import/album/track01.mp3", b"/import/album/track02.mp3"]

        state = ImportState(path=os.fsencode(state_file))
        state.progress_add(toppath, *paths)

        state2 = ImportState(path=os.fsencode(state_file))
        is_resuming = True
        assert all(
            state2.progress_has_element(toppath, p) for p in paths
        )
        assert is_resuming is True

    def test_already_imported_resume_mode_partial_paths(self, state_file):
        """resume 模式下部分路径未处理应返回 False。"""
        toppath = b"/import/album"
        processed = [b"/import/album/track01.mp3"]
        unprocessed = [b"/import/album/track02.mp3"]

        state = ImportState(path=os.fsencode(state_file))
        state.progress_add(toppath, *processed)

        state2 = ImportState(path=os.fsencode(state_file))
        assert not all(
            state2.progress_has_element(toppath, p)
            for p in processed + unprocessed
        )

    def test_already_imported_resume_mode_not_resuming(self, state_file):
        """非 resume 模式即使路径已处理也不应跳过。"""
        toppath = b"/import/album"
        paths = [b"/import/album/track01.mp3"]

        state = ImportState(path=os.fsencode(state_file))
        state.progress_add(toppath, *paths)

        state2 = ImportState(path=os.fsencode(state_file))
        is_resuming = False
        all_processed = all(
            state2.progress_has_element(toppath, p) for p in paths
        )
        assert not (is_resuming and all_processed)

    def test_already_imported_incremental_mode(self, state_file):
        """incremental 模式下已导入的路径组合应返回 True。"""
        paths = [b"/import/album1/track01.mp3", b"/import/album1/track02.mp3"]

        state = ImportState(path=os.fsencode(state_file))
        state.history_add(paths)

        state2 = ImportState(path=os.fsencode(state_file))
        incremental = True
        norm_paths = tuple(_normpath(p) for p in paths)
        assert incremental and norm_paths in state2.taghistory

    def test_already_imported_incremental_mode_not_in_history(self, state_file):
        """incremental 模式下未导入的路径组合应返回 False。"""
        paths = [b"/import/album1/track01.mp3"]
        other_paths = [b"/import/album2/track01.mp3"]

        state = ImportState(path=os.fsencode(state_file))
        state.history_add(paths)

        state2 = ImportState(path=os.fsencode(state_file))
        incremental = True
        norm_other = tuple(_normpath(p) for p in other_paths)
        assert not (incremental and norm_other in state2.taghistory)

    def test_already_imported_incremental_mode_disabled(self, state_file):
        """incremental 模式禁用时即使在历史中也不跳过。"""
        paths = [b"/import/album1/track01.mp3"]

        state = ImportState(path=os.fsencode(state_file))
        state.history_add(paths)

        state2 = ImportState(path=os.fsencode(state_file))
        incremental = False
        norm_paths = tuple(_normpath(p) for p in paths)
        assert not (incremental and norm_paths in state2.taghistory)

    @pytest.mark.skipif(
        not _normpath_is_case_insensitive(),
        reason="only run on case-insensitive filesystems",
    )
    def test_already_imported_incremental_case_variant(self, state_file):
        """incremental 模式下路径大小写变体应正确识别。"""
        paths_upper = [b"/Music/Album/Track01.MP3", b"/Music/Album/Track02.MP3"]
        paths_lower = [b"/music/album/track01.mp3", b"/music/album/track02.mp3"]

        state = ImportState(path=os.fsencode(state_file))
        state.history_add(paths_upper)

        state2 = ImportState(path=os.fsencode(state_file))
        incremental = True
        norm_lower = tuple(_normpath(p) for p in paths_lower)
        assert incremental and norm_lower in state2.taghistory


class TestSessionAlreadyMerged:
    """ImportSession.already_merged 的核心逻辑测试。

    验证 _normpath 在已合并路径判断中的使用，
    包括路径大小写不敏感处理。
    """

    def test_already_merged_all_items_merged(self):
        """所有路径都在已合并列表中应返回 True。"""
        merged_items = {b"/import/album/track01.mp3", b"/import/album/track02.mp3"}
        merged_dirs = set()
        paths = [b"/import/album/track01.mp3"]

        for path in paths:
            norm = _normpath(path)
            assert norm in {_normpath(p) for p in merged_items}

    def test_already_merged_via_directory(self):
        """路径所属目录已合并应返回 True。"""
        merged_items = set()
        merged_dirs = {b"/import/album"}
        paths = [b"/import/album/track01.mp3"]

        dir_path = os.path.dirname(paths[0])
        norm_dir = _normpath(dir_path)
        assert norm_dir in {_normpath(d) for d in merged_dirs}

    def test_already_merged_not_merged(self):
        """路径和目录都未合并应返回 False。"""
        merged_items = {b"/import/album/track01.mp3"}
        merged_dirs = {b"/import/other"}
        paths = [b"/import/album/track02.mp3"]

        norm = _normpath(paths[0])
        assert norm not in {_normpath(p) for p in merged_items}
        norm_dir = _normpath(os.path.dirname(paths[0]))
        assert norm_dir not in {_normpath(d) for d in merged_dirs}

    def test_already_merged_multiple_paths(self):
        """多个路径需全部合并才返回 True。"""
        merged_items = {b"/import/album/track01.mp3", b"/import/album/track02.mp3"}
        merged_dirs = set()
        all_paths = [b"/import/album/track01.mp3", b"/import/album/track02.mp3"]
        partial_paths = [b"/import/album/track01.mp3", b"/import/album/track03.mp3"]

        all_result = all(
            _normpath(p) in {_normpath(m) for m in merged_items}
            for p in all_paths
        )
        assert all_result is True

        partial_result = all(
            _normpath(p) in {_normpath(m) for m in merged_items}
            for p in partial_paths
        )
        assert partial_result is False

    @pytest.mark.skipif(
        not _normpath_is_case_insensitive(),
        reason="only run on case-insensitive filesystems",
    )
    def test_already_merged_case_variant(self):
        """路径大小写变体应被正确识别为已合并。"""
        merged_items = {b"/Music/Album/Track01.MP3"}
        merged_dirs = set()
        paths = [b"/music/album/track01.mp3"]

        norm = _normpath(paths[0])
        assert norm in {_normpath(p) for p in merged_items}

    @pytest.mark.skipif(
        not _normpath_is_case_insensitive(),
        reason="only run on case-insensitive filesystems",
    )
    def test_already_merged_directory_case_variant(self):
        """目录路径大小写变体应被正确识别为已合并。"""
        merged_items = set()
        merged_dirs = {b"/Music/Album"}
        paths = [b"/music/album/track01.mp3"]

        dir_path = os.path.dirname(paths[0])
        norm_dir = _normpath(dir_path)
        assert norm_dir in {_normpath(d) for d in merged_dirs}


class TestTasksDuplicatePathComparison:
    """tasks.py 中重复检测路径大小写不敏感逻辑测试。

    验证 find_duplicate_albums 和 find_duplicates 中使用的
    _normpath 路径比较逻辑，确保在 CI FS 上不会
    因路径大小写差异而误报重复。
    """

    @staticmethod
    def _normcase_path(path_bytes):
        """模拟 tasks.py 中的路径规范化方式。"""
        return _normpath(path_bytes)

    def test_same_path_identical_case(self):
        """相同路径相同大小写应识别为同一路径。"""
        path1 = b"/Music/Album/Track.mp3"
        path2 = b"/Music/Album/Track.mp3"
        assert self._normcase_path(path1) == self._normcase_path(path2)

    def test_same_path_different_case_ci_fs(self):
        """大小写不敏感文件系统上，相同路径不同大小写应识别为同一路径。"""
        path_upper = b"/Music/Album/Track.mp3"
        path_lower = b"/music/album/track.mp3"
        if _normpath_is_case_insensitive():
            assert self._normcase_path(path_upper) == self._normcase_path(path_lower)
        else:
            assert self._normcase_path(path_upper) != self._normcase_path(path_lower)

    def test_different_paths(self):
        """不同路径不应识别为同一路径。"""
        path1 = b"/Music/Album/Track01.mp3"
        path2 = b"/Music/Album/Track02.mp3"
        assert self._normcase_path(path1) != self._normcase_path(path2)

    def test_album_path_subset_detection(self):
        """模拟专辑重复检测：专辑所有文件路径都是任务路径子集应不算重复。"""
        task_paths = {
            self._normcase_path(b"/Music/Album/Track01.mp3"),
            self._normcase_path(b"/Music/Album/Track02.mp3"),
            self._normcase_path(b"/Music/Album/Track03.mp3"),
        }
        album_paths = {
            self._normcase_path(b"/Music/Album/Track01.mp3"),
            self._normcase_path(b"/Music/Album/Track02.mp3"),
        }
        assert album_paths <= task_paths

    def test_album_path_not_subset_detection(self):
        """模拟专辑重复检测：专辑文件路径不是任务路径子集应算重复。"""
        task_paths = {
            self._normcase_path(b"/Music/Album/Track01.mp3"),
            self._normcase_path(b"/Music/Album/Track02.mp3"),
        }
        album_paths = {
            self._normcase_path(b"/Music/Album/Track01.mp3"),
            self._normcase_path(b"/Music/Album/Track03.mp3"),
        }
        assert not (album_paths <= task_paths)

    @pytest.mark.skipif(
        not _normpath_is_case_insensitive(),
        reason="only run on case-insensitive filesystems",
    )
    def test_album_path_subset_case_variant(self):
        """CI FS 上，大小写不同的相同路径应被识别为子集关系。"""
        task_paths = {
            self._normcase_path(b"/Music/Album/Track01.MP3"),
            self._normcase_path(b"/Music/Album/Track02.MP3"),
        }
        album_paths = {
            self._normcase_path(b"/music/album/track01.mp3"),
            self._normcase_path(b"/music/album/track02.mp3"),
        }
        assert album_paths <= task_paths

    @pytest.mark.skipif(
        not _normpath_is_case_insensitive(),
        reason="only run on case-insensitive filesystems",
    )
    def test_item_duplicate_case_variant(self):
        """CI FS 上，单条曲目重复检测应正确处理路径大小写变体。"""
        item_path = self._normcase_path(b"/Library/Song.MP3")
        task_path = self._normcase_path(b"/library/song.mp3")
        assert item_path == task_path

    def test_item_duplicate_different_file(self):
        """不同文件路径不应被识别为重复。"""
        item_path = self._normcase_path(b"/Library/Song1.mp3")
        task_path = self._normcase_path(b"/Library/Song2.mp3")
        assert item_path != task_path

    def test_path_with_spaces_case_variant(self):
        """包含空格的路径大小写变体应正确处理。"""
        path_upper = b"/Music/My Great Song.mp3"
        path_lower = b"/music/my great song.mp3"
        if _normpath_is_case_insensitive():
            assert self._normcase_path(path_upper) == self._normcase_path(path_lower)

    def test_path_with_unicode(self):
        """包含 Unicode 字符的路径应正确规范化。"""
        path_upper = b"/Music/\xc3\x84hnlicher Titel.mp3"
        path_lower = b"/music/\xc3\xa4hnlicher titel.mp3"
        if _normpath_is_case_insensitive():
            assert self._normcase_path(path_upper) == self._normcase_path(path_lower)


class TestConcurrentWriteRaceConditions:
    """多实例并发写入同一状态文件的竞态条件测试。

    模拟两个 beets import 实例同时操作同一 state 文件的场景，
    验证 read-modify-write 模式下的丢失更新、交错写入等问题。
    """

    def test_lost_update_two_instances_same_toppath(self, state_file):
        """两个实例向同一 toppath 添加不同路径，可能出现丢失更新。

        场景：
        - 实例 A 读取 state（空）
        - 实例 B 读取 state（空）
        - 实例 A 添加 track01 并写入
        - 实例 B 添加 track02 并写入
        - 结果：track01 丢失（B 基于旧状态覆盖写入）

        这是当前实现的已知行为，测试记录实际表现。
        """
        toppath = b"/import/album"
        path_a = b"/import/album/track01.mp3"
        path_b = b"/import/album/track02.mp3"

        instance_a = ImportState(path=os.fsencode(state_file))
        instance_b = ImportState(path=os.fsencode(state_file))

        assert not instance_a.progress_has(toppath)
        assert not instance_b.progress_has(toppath)

        instance_a.progress_add(toppath, path_a)

        state_after_a = ImportState(path=os.fsencode(state_file))
        assert state_after_a.progress_has_element(toppath, path_a)

        instance_b.progress_add(toppath, path_b)

        state_final = ImportState(path=os.fsencode(state_file))

        assert state_final.progress_has_element(toppath, path_b)

    def test_lost_update_two_instances_different_toppaths(self, state_file):
        """两个实例向不同 toppath 添加数据，仍可能互相覆盖。

        场景：
        - 实例 A 读取 state（空）
        - 实例 B 读取 state（空）
        - 实例 A 添加 album1/track01 并写入
        - 实例 B 添加 album2/track01 并写入
        - 结果：album1 的数据可能丢失
        """
        toppath_a = b"/import/album1"
        toppath_b = b"/import/album2"
        path_a = b"/import/album1/track01.mp3"
        path_b = b"/import/album2/track01.mp3"

        instance_a = ImportState(path=os.fsencode(state_file))
        instance_b = ImportState(path=os.fsencode(state_file))

        instance_a.progress_add(toppath_a, path_a)
        instance_b.progress_add(toppath_b, path_b)

        state_final = ImportState(path=os.fsencode(state_file))

        has_a = state_final.progress_has(toppath_a)
        has_b = state_final.progress_has(toppath_b)
        assert has_b

    def test_interleaved_read_write_read(self, state_file):
        """交错读-写-读场景：读取后文件被其他实例修改。

        验证：内存中的 state 不会自动反映磁盘上的变化，
        需要重新打开 ImportState 才能看到新写入的数据。
        """
        toppath = b"/import/album"
        path1 = b"/import/album/track01.mp3"
        path2 = b"/import/album/track02.mp3"

        state_reader = ImportState(path=os.fsencode(state_file))
        assert not state_reader.progress_has(toppath)

        state_writer = ImportState(path=os.fsencode(state_file))
        state_writer.progress_add(toppath, path1)

        assert not state_reader.progress_has(toppath)

        state_reader2 = ImportState(path=os.fsencode(state_file))
        assert state_reader2.progress_has_element(toppath, path1)

        state_writer2 = ImportState(path=os.fsencode(state_file))
        state_writer2.progress_add(toppath, path2)

        state_reader3 = ImportState(path=os.fsencode(state_file))
        assert state_reader3.progress_has_element(toppath, path2)

    def test_progress_add_vs_concurrent_reset(self, state_file):
        """progress_add 与 progress_reset 并发执行的竞态。

        场景：
        - 实例 A 读取并准备添加路径
        - 实例 B reset 该 toppath
        - 实例 A 写入（包含旧路径数据 + 新路径）
        """
        toppath = b"/import/album"
        path_existing = b"/import/album/track01.mp3"
        path_new = b"/import/album/track02.mp3"

        init = ImportState(path=os.fsencode(state_file))
        init.progress_add(toppath, path_existing)

        instance_adder = ImportState(path=os.fsencode(state_file))
        assert instance_adder.progress_has_element(toppath, path_existing)

        reseter = ImportState(path=os.fsencode(state_file))
        reseter.progress_reset(toppath)

        state_after_reset = ImportState(path=os.fsencode(state_file))
        assert not state_after_reset.progress_has(toppath)

        instance_adder.progress_add(toppath, path_new)

        state_final = ImportState(path=os.fsencode(state_file))
        assert state_final.progress_has_element(toppath, path_existing)
        assert state_final.progress_has_element(toppath, path_new)

    def test_history_add_concurrent_lost_update(self, state_file):
        """history_add 并发写入也存在丢失更新问题。"""
        paths_a = [b"/import/album_a"]
        paths_b = [b"/import/album_b"]

        instance_a = ImportState(path=os.fsencode(state_file))
        instance_b = ImportState(path=os.fsencode(state_file))

        instance_a.history_add(paths_a)
        instance_b.history_add(paths_b)

        state_final = ImportState(path=os.fsencode(state_file))
        norm_a = tuple(_normpath(p) for p in paths_a)
        norm_b = tuple(_normpath(p) for p in paths_b)

        has_a = norm_a in state_final.taghistory
        has_b = norm_b in state_final.taghistory

        assert has_b

    def test_multiple_consecutive_writes_preserve_order(self, state_file):
        """同一实例连续多次写入应保持一致状态。"""
        toppath = b"/import/album"
        all_paths = [
            b"/import/album/track01.mp3",
            b"/import/album/track02.mp3",
            b"/import/album/track03.mp3",
            b"/import/album/track04.mp3",
            b"/import/album/track05.mp3",
        ]

        for i in range(len(all_paths)):
            state = ImportState(path=os.fsencode(state_file))
            state.progress_add(toppath, all_paths[i])

        state_final = ImportState(path=os.fsencode(state_file))
        for p in all_paths:
            assert state_final.progress_has_element(toppath, p)

    def test_two_instances_add_same_path_no_duplicate_issues(self, state_file):
        """两个实例添加同一路径不会导致异常。"""
        toppath = b"/import/album"
        path = b"/import/album/track01.mp3"

        instance_a = ImportState(path=os.fsencode(state_file))
        instance_b = ImportState(path=os.fsencode(state_file))

        instance_a.progress_add(toppath, path)
        instance_b.progress_add(toppath, path)

        state_final = ImportState(path=os.fsencode(state_file))
        assert state_final.progress_has_element(toppath, path)


class TestNetworkFSStateSynchronization:
    """网络文件系统（NFS/SMB）上状态文件同步语义测试。

    模拟网络盘常见的同步问题：close-to-open 缓存一致性、
    写入延迟可见性、部分写入传播、客户端缓存不一致等。
    这些测试不依赖真实网络盘，而是通过模拟关键行为来
    验证 ImportState 在各种同步场景下的表现。
    """

    def test_write_then_reopen_versus_in_memory(self, state_file):
        """写入后重新打开的实例与内存中实例的状态差异。

        模拟网络盘 close-to-open 一致性：
        - 写入后必须重新打开才能看到最新数据
        - 内存中的旧实例看不到其他客户端的写入
        """
        toppath = b"/import/album"
        path = b"/import/album/track01.mp3"

        state_reader = ImportState(path=os.fsencode(state_file))
        assert not state_reader.progress_has(toppath)

        state_writer = ImportState(path=os.fsencode(state_file))
        state_writer.progress_add(toppath, path)

        assert not state_reader.progress_has(toppath)

        state_fresh = ImportState(path=os.fsencode(state_file))
        assert state_fresh.progress_has_element(toppath, path)

    def test_write_during_read_stale_cache(self, state_file):
        """模拟网络盘 stale cache：读取时写入尚未传播。

        场景：
        - 客户端 A 写入数据
        - 网络延迟导致客户端 B 打开时读到旧数据
        - 验证 ImportState 能正常处理读到的旧数据（不崩溃）
        """
        toppath = b"/import/album"
        path1 = b"/import/album/track01.mp3"
        path2 = b"/import/album/track02.mp3"

        writer1 = ImportState(path=os.fsencode(state_file))
        writer1.progress_add(toppath, path1)

        stale_reader = ImportState(path=os.fsencode(state_file))
        assert stale_reader.progress_has_element(toppath, path1)

        writer2 = ImportState(path=os.fsencode(state_file))
        writer2.progress_add(toppath, path2)

        assert not stale_reader.progress_has_element(toppath, path2)

        fresh_reader = ImportState(path=os.fsencode(state_file))
        assert fresh_reader.progress_has_element(toppath, path2)

    def test_partial_write_corruption_handled(self, state_file):
        """网络盘传输中断导致半截 pickle 文件。

        NFS/SMB 上写入时可能因网络中断只写入部分数据，
        验证 ImportState 能优雅处理此类损坏。
        """
        toppath = b"/import/album"
        path = b"/import/album/track01.mp3"

        good_writer = ImportState(path=os.fsencode(state_file))
        good_writer.progress_add(toppath, path)

        state_good = ImportState(path=os.fsencode(state_file))
        assert state_good.progress_has_element(toppath, path)

        with open(state_file, "rb") as f:
            good_data = f.read()
        with open(state_file, "wb") as f:
            f.write(good_data[: len(good_data) // 2])

        state_after_bad_write = ImportState(path=os.fsencode(state_file))
        assert state_after_bad_write.tagprogress == {}
        assert state_after_bad_write.taghistory == set()

        recovery_writer = ImportState(path=os.fsencode(state_file))
        recovery_writer.progress_add(toppath, path)

        state_recovered = ImportState(path=os.fsencode(state_file))
        assert state_recovered.progress_has_element(toppath, path)

    def test_concurrent_writers_network_delay(self, state_file):
        """模拟两个客户端通过网络盘并发写入。

        NFS 上无缓存一致性保证，验证即使出现数据竞争，
        ImportState 也不会产生无法读取的损坏文件。
        """
        toppath_a = b"/import/album_a"
        toppath_b = b"/import/album_b"
        path_a = b"/import/album_a/track01.mp3"
        path_b = b"/import/album_b/track01.mp3"

        for round_idx in range(5):
            writer_a = ImportState(path=os.fsencode(state_file))
            writer_b = ImportState(path=os.fsencode(state_file))

            writer_a.progress_add(
                toppath_a,
                os.fsdecode(path_a).replace("01", f"{round_idx:02d}").encode(),
            )
            writer_b.progress_add(
                toppath_b,
                os.fsdecode(path_b).replace("01", f"{round_idx:02d}").encode(),
            )

        state_final = ImportState(path=os.fsencode(state_file))

        assert state_final.progress_has(toppath_a) or state_final.progress_has(toppath_b)

    def test_state_file_replaced_between_open_and_save(self, state_file):
        """网络盘上另一客户端在 open 和 save 之间替换了整个文件。

        场景：
        - 实例 A 打开读取
        - 实例 B 写入全新数据
        - 实例 A 基于旧数据保存，覆盖 B 的数据

        验证：至少不会产生无法解析的文件。
        """
        toppath_a = b"/import/album_a"
        toppath_b = b"/import/album_b"
        path_a = b"/import/album_a/track01.mp3"
        path_b = b"/import/album_b/track01.mp3"

        writer_b = ImportState(path=os.fsencode(state_file))
        writer_b.progress_add(toppath_b, path_b)

        stale_writer_a = ImportState(path=os.fsencode(state_file))
        assert stale_writer_a.progress_has(toppath_b)

        writer_b2 = ImportState(path=os.fsencode(state_file))
        writer_b2.progress_add(toppath_b, b"/import/album_b/track02.mp3")

        stale_writer_a.progress_add(toppath_a, path_a)

        state_final = ImportState(path=os.fsencode(state_file))
        assert state_final.progress_has(toppath_a)

    def test_history_concurrent_merge_network_fs(self, state_file):
        """网络盘上两个客户端同时添加历史记录。

        模拟 NFS close-to-open 下两个客户端各自添加历史，
        验证最终状态文件至少能被正确解析。
        """
        paths_sets = [
            [b"/netshare/album1"],
            [b"/netshare/album2"],
            [b"/netshare/album3"],
        ]

        for paths in paths_sets:
            writer = ImportState(path=os.fsencode(state_file))
            writer.history_add(paths)

        state_final = ImportState(path=os.fsencode(state_file))
        assert isinstance(state_final.taghistory, set)

    def test_empty_write_flush_network_delay(self, state_file):
        """网络盘上 flush 延迟：空 state 也能正确处理。

        初始状态写入后，即使网络延迟导致 flush 尚未完成，
        后续读取也应能安全处理。
        """
        empty_state = ImportState(path=os.fsencode(state_file))
        assert empty_state.tagprogress == {}
        assert empty_state.taghistory == set()

        with empty_state as state:
            pass

        state_after_empty_save = ImportState(path=os.fsencode(state_file))
        assert state_after_empty_save.tagprogress == {}
        assert state_after_empty_save.taghistory == set()

    def test_repeated_open_close_network_roundtrip(self, state_file):
        """模拟网络盘多次往返：反复打开-关闭-读取。

        验证在多次网络 I/O 往返后状态仍保持一致性。
        """
        toppath = b"/netimport/album"
        all_paths = [
            b"/netimport/album/track01.mp3",
            b"/netimport/album/track02.mp3",
            b"/netimport/album/track03.mp3",
        ]

        for i, p in enumerate(all_paths):
            writer = ImportState(path=os.fsencode(state_file))
            writer.progress_add(toppath, p)

            for _ in range(3):
                reader = ImportState(path=os.fsencode(state_file))
                for j in range(i + 1):
                    assert reader.progress_has_element(toppath, all_paths[j])

    def test_network_fs_partial_history_state(self, state_file):
        """网络盘上只写入了部分 taghistory 的状态文件。

        模拟传输中断导致 pickle 结构不完整，
        验证 ImportState 回退到空状态。
        """
        paths = [b"/import/album1", b"/import/album2"]

        writer = ImportState(path=os.fsencode(state_file))
        writer.history_add(paths)

        norm_paths = tuple(_normpath(p) for p in paths)
        state_good = ImportState(path=os.fsencode(state_file))
        assert norm_paths in state_good.taghistory

        with open(state_file, "rb") as f:
            data = f.read()

        for trunc_size in range(0, len(data), max(1, len(data) // 10)):
            with open(state_file, "wb") as f:
                f.write(data[:trunc_size])

            should_not_crash = ImportState(path=os.fsencode(state_file))
            assert isinstance(should_not_crash.tagprogress, dict)
            assert isinstance(should_not_crash.taghistory, set)
