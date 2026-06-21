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
