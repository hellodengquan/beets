"""Tests for cross-platform path utilities: syspath, normpath, and path_as_posix."""

import os

import pytest

from beets.util import normpath, path_as_posix, syspath


@pytest.mark.parametrize(
    "path_input",
    [b"/test/path", "/simple/path"],
    ids=["bytes_input", "str_input"],
)
def test_syspath_returns_string(path_input):
    result = syspath(path_input)
    assert isinstance(result, str)


def test_syspath_no_change_on_posix():
    if os.name != "nt":
        result = syspath("/test/path")
        assert result == "/test/path"


@pytest.mark.windows
@pytest.mark.parametrize(
    "path_input,prefix,expected_start,expected_eq",
    [
        ("C:\\test\\path", True, "\\\\?\\", None),
        ("C:\\test\\path", False, None, "C:\\test\\path"),
        ("\\\\server\\share\\path", True, "\\\\?\\UNC\\", None),
    ],
    ids=["windows_prefix", "windows_no_prefix", "windows_unc"],
)
def test_syspath_windows(path_input, prefix, expected_start, expected_eq):
    result = syspath(path_input, prefix=prefix)
    if expected_start:
        assert result.startswith(expected_start)
    if expected_eq:
        assert result == expected_eq


@pytest.mark.parametrize(
    "path_input,check",
    [
        ("/test/path", lambda r: isinstance(r, bytes)),
        ("test/path", lambda r: os.path.isabs(os.fsdecode(r))),
        ("~/test", lambda r: b"~" not in r),
        ("/test/../test/./path", lambda r: ".." not in os.fsdecode(r) and "." not in os.fsdecode(r).split(os.sep)),
    ],
    ids=["returns_bytes", "absolute", "expands_user", "normalizes_separators"],
)
def test_normpath(path_input, check):
    result = normpath(path_input)
    assert check(result)


@pytest.mark.parametrize(
    "path_input,expected",
    [
        (b"test\\path\\to\\file", b"test/path/to/file"),
        (b"/test/path", b"/test/path"),
        (b"test/path\\to/file", b"test/path/to/file"),
    ],
    ids=["replaces_backslashes", "no_change_posix", "mixed_separators"],
)
def test_path_as_posix(path_input, expected):
    result = path_as_posix(path_input)
    assert isinstance(result, bytes)
    assert result == expected


@pytest.mark.parametrize(
    "check",
    [
        lambda: os.sep in ("/", "\\"),
        lambda: os.sep in os.fsdecode(normpath("/test/path")),
    ],
    ids=["os_sep_valid", "normpath_uses_sep"],
)
def test_path_separator(check):
    assert check()


@pytest.mark.posix
def test_posix_path_separator():
    assert os.sep == "/"
    assert b"/" in normpath("/test/path")


@pytest.mark.windows
def test_windows_path_separator():
    assert os.sep == "\\"


@pytest.mark.posix
def test_posix_case_sensitive_paths():
    result_upper = normpath("/TEST/PATH")
    result_lower = normpath("/test/path")
    decoded_upper = os.fsdecode(result_upper)
    decoded_lower = os.fsdecode(result_lower)
    assert decoded_upper.lower() == decoded_lower.lower()
    assert decoded_upper != decoded_lower or "home" in decoded_lower.lower()


@pytest.mark.windows
def test_windows_case_insensitive_comparison():
    path_upper = os.path.normcase("/TEST/PATH")
    path_lower = os.path.normcase("/test/path")
    assert path_upper == path_lower


@pytest.mark.posix
def test_posix_allows_colon():
    result = normpath("/test:path")
    assert b":" in result


@pytest.mark.parametrize(
    "path_input,expected_char,use_syspath",
    [
        ("/test path with spaces", b" ", False),
        ("/test_@#$%", "@#$%", True),
    ],
    ids=["spaces_preserved", "special_chars_preserved"],
)
def test_reserved_characters(path_input, expected_char, use_syspath):
    if use_syspath:
        result = syspath(path_input)
    else:
        result = normpath(path_input)
    if isinstance(expected_char, bytes):
        assert expected_char in result
    else:
        for ch in expected_char:
            assert ch in result


@pytest.mark.posix
def test_posix_allows_backslash_in_filename():
    result = syspath("/test\\path")
    assert "\\" in result
