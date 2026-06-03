"""Tests for cross-platform path utilities: syspath, normpath, and path_as_posix."""

import os

import pytest

from beets.util import normpath, path_as_posix, syspath


class TestSyspathCrossPlatform:
    def test_syspath_returns_string(self):
        """syspath should always return a string."""
        result = syspath(b"/test/path")
        assert isinstance(result, str)

    def test_syspath_bytes_input(self):
        """syspath should accept bytes input."""
        result = syspath(b"/simple/path")
        assert isinstance(result, str)

    def test_syspath_str_input(self):
        """syspath should accept string input."""
        result = syspath("/simple/path")
        assert isinstance(result, str)

    def test_syspath_no_change_on_posix(self):
        """On POSIX, syspath should return the path unchanged."""
        if os.name != "nt":
            result = syspath("/test/path")
            assert result == "/test/path"

    @pytest.mark.windows
    def test_syspath_windows_prefix(self):
        """On Windows, syspath should add the magic prefix."""
        result = syspath("C:\\test\\path")
        assert result.startswith("\\\\?\\")

    @pytest.mark.windows
    def test_syspath_windows_no_prefix_option(self):
        """On Windows, syspath with prefix=False should not add prefix."""
        result = syspath("C:\\test\\path", prefix=False)
        assert not result.startswith("\\\\?\\")
        assert result == "C:\\test\\path"

    @pytest.mark.windows
    def test_syspath_windows_unc_path(self):
        """On Windows, UNC paths should get special handling."""
        result = syspath("\\\\server\\share\\path")
        assert result.startswith("\\\\?\\UNC\\")


class TestNormpathCrossPlatform:
    def test_normpath_returns_bytes(self):
        """normpath should always return bytes."""
        result = normpath("/test/path")
        assert isinstance(result, bytes)

    def test_normpath_absolute_path(self):
        """normpath should return absolute paths."""
        result = normpath("test/path")
        assert os.path.isabs(os.fsdecode(result))

    def test_normpath_expands_user(self):
        """normpath should expand user home directory."""
        result = normpath("~/test")
        assert b"~" not in result

    def test_normpath_normalizes_separators(self):
        """normpath should normalize path separators."""
        result = normpath("/test/../test/./path")
        decoded = os.fsdecode(result)
        assert ".." not in decoded
        assert "." not in decoded.split(os.sep)


class TestPathAsPosixCrossPlatform:
    def test_path_as_posix_returns_bytes(self):
        """path_as_posix should always return bytes."""
        result = path_as_posix(b"test\\path")
        assert isinstance(result, bytes)

    def test_path_as_posix_replaces_backslashes(self):
        """path_as_posix should replace backslashes with forward slashes."""
        result = path_as_posix(b"test\\path\\to\\file")
        assert result == b"test/path/to/file"

    def test_path_as_posix_no_change_for_posix_paths(self):
        """path_as_posix should leave POSIX paths unchanged."""
        result = path_as_posix(b"/test/path")
        assert result == b"/test/path"

    def test_path_as_posix_mixed_separators(self):
        """path_as_posix should handle mixed separators."""
        result = path_as_posix(b"test/path\\to/file")
        assert result == b"test/path/to/file"


class TestPathSeparatorBehavior:
    """Test platform-specific path separator behavior."""

    def test_os_sep_exists(self):
        """os.sep should be correct for the platform."""
        assert os.sep in ("/", "\\")

    def test_path_sep_used_in_normpath(self):
        """normpath should use the correct separator."""
        result = normpath("/test/path")
        decoded = os.fsdecode(result)
        assert os.sep in decoded

    @pytest.mark.posix
    def test_posix_path_separator(self):
        """On POSIX, paths should use forward slash."""
        assert os.sep == "/"
        result = normpath("/test/path")
        assert b"/" in result

    @pytest.mark.windows
    def test_windows_path_separator(self):
        """On Windows, paths should use backslash."""
        assert os.sep == "\\"


class TestCaseSensitivity:
    """Test platform-specific case sensitivity behavior."""

    @pytest.mark.posix
    def test_posix_case_sensitive_paths(self):
        """On POSIX, paths should preserve case in normpath."""
        result_upper = normpath("/TEST/PATH")
        result_lower = normpath("/test/path")
        decoded_upper = os.fsdecode(result_upper)
        decoded_lower = os.fsdecode(result_lower)
        assert decoded_upper.lower() == decoded_lower.lower()
        assert decoded_upper != decoded_lower or "home" in decoded_lower.lower()

    @pytest.mark.windows
    def test_windows_case_insensitive_comparison(self):
        """On Windows, normcase should make paths case-insensitive."""
        path_upper = os.path.normcase("/TEST/PATH")
        path_lower = os.path.normcase("/test/path")
        assert path_upper == path_lower


class TestReservedCharacters:
    """Test platform-specific reserved character handling."""

    @pytest.mark.posix
    def test_posix_allows_colon(self):
        """On POSIX, colons are allowed in paths."""
        result = normpath("/test:path")
        assert b":" in result

    def test_path_with_spaces(self):
        """Spaces should be preserved."""
        result = normpath("/test path with spaces")
        assert b" " in result

    def test_path_with_special_chars(self):
        """Special characters should be preserved."""
        result = syspath("/test_@#$%")
        assert "@" in result
        assert "#" in result
        assert "$" in result
        assert "%" in result

    @pytest.mark.posix
    def test_posix_allows_backslash_in_filename(self):
        """On POSIX, backslashes are allowed in filenames."""
        result = syspath("/test\\path")
        assert "\\" in result
