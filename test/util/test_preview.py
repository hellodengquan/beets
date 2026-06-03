"""Tests for the preview module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from beets.exceptions import UserError
from beets.util.functemplate import template
from beets.util.pathformats import PF_KEY_DEFAULT
from beets.util.preview import (
    _check_missing_fields,
    _extract_template_fields,
    _get_matched_path_format,
    preview_paths,
)


class TestExtractTemplateFields:
    """Tests for _extract_template_fields function."""

    def test_simple_fields(self):
        """Extract field names from a simple template."""
        fields = _extract_template_fields("$artist/$album/$title")
        assert "artist" in fields
        assert "album" in fields
        assert "title" in fields

    def test_with_function_call(self):
        """Extract field names from template with function calls."""
        fields = _extract_template_fields("$artist/${album}%aunique{}")
        assert "artist" in fields
        assert "album" in fields

    def test_empty_template(self):
        """Extract fields from empty template."""
        fields = _extract_template_fields("")
        assert fields == set()

    def test_plain_text_only(self):
        """Extract fields from template with only plain text."""
        fields = _extract_template_fields("hello world")
        assert fields == set()

    def test_template_object(self):
        """Extract fields from a Template object."""
        tmpl = template("$artist/$title")
        fields = _extract_template_fields(tmpl)
        assert "artist" in fields
        assert "title" in fields


class TestCheckMissingFields:
    """Tests for _check_missing_fields function."""

    def test_no_missing_fields(self):
        """All fields exist and have non-empty values."""
        mock_obj = MagicMock()
        mock_obj.formatted.return_value = {
            "artist": "Beatles",
            "album": "Abbey Road",
            "title": "Come Together",
        }
        missing = _check_missing_fields(mock_obj, ["artist", "album", "title"])
        assert missing == []

    def test_missing_some_fields(self):
        """Some fields are missing (not in formatted dict)."""
        mock_obj = MagicMock()
        mock_obj.formatted.return_value = {
            "artist": "Beatles",
            "title": "Come Together",
        }
        missing = _check_missing_fields(mock_obj, ["artist", "album", "title"])
        assert "album" in missing
        assert "artist" not in missing

    def test_empty_string_fields(self):
        """Fields exist but have empty string values."""
        mock_obj = MagicMock()
        mock_obj.formatted.return_value = {
            "artist": "Beatles",
            "album": "",
            "title": "Come Together",
        }
        missing = _check_missing_fields(mock_obj, ["artist", "album", "title"])
        assert "album" in missing

    def test_whitespace_only_fields(self):
        """Fields exist but have only whitespace values."""
        mock_obj = MagicMock()
        mock_obj.formatted.return_value = {
            "artist": "Beatles",
            "album": "   ",
            "title": "Come Together",
        }
        missing = _check_missing_fields(mock_obj, ["artist", "album", "title"])
        assert "album" in missing

    def test_none_value_fields(self):
        """Fields exist but have None values."""
        mock_obj = MagicMock()
        mock_obj.formatted.return_value = {
            "artist": "Beatles",
            "album": None,
            "title": "Come Together",
        }
        missing = _check_missing_fields(mock_obj, ["artist", "album", "title"])
        assert "album" in missing


class TestGetMatchedPathFormat:
    """Tests for _get_matched_path_format function."""

    def test_default_rule(self):
        """Fallback to default rule when no query matches."""
        mock_obj = MagicMock()
        mock_obj.__class__ = MagicMock()

        default_tmpl = template("$artist/$album/$title")
        path_formats = [(PF_KEY_DEFAULT, default_tmpl)]

        query, tmpl = _get_matched_path_format(mock_obj, path_formats)
        assert query == PF_KEY_DEFAULT
        assert tmpl is default_tmpl

    def test_query_rule_matches(self):
        """Matching query rule is returned."""
        mock_obj = MagicMock()
        mock_obj.__class__ = MagicMock()

        default_tmpl = template("$artist/$album/$title")
        singleton_tmpl = template("Singles/$title")
        path_formats = [
            ("singleton:true", singleton_tmpl),
            (PF_KEY_DEFAULT, default_tmpl),
        ]

        with patch("beets.library.queries.parse_query_string") as mock_parse:
            mock_q = MagicMock()
            mock_q.match.return_value = True
            mock_parse.return_value = (mock_q, None)

            query, tmpl = _get_matched_path_format(mock_obj, path_formats)
            assert query == "singleton:true"
            assert tmpl is singleton_tmpl

    def test_query_rule_no_match(self):
        """Non-matching query rule is skipped, falls back to default."""
        mock_obj = MagicMock()
        mock_obj.__class__ = MagicMock()

        default_tmpl = template("$artist/$album/$title")
        singleton_tmpl = template("Singles/$title")
        path_formats = [
            ("singleton:true", singleton_tmpl),
            (PF_KEY_DEFAULT, default_tmpl),
        ]

        with patch("beets.library.queries.parse_query_string") as mock_parse:
            mock_q = MagicMock()
            mock_q.match.return_value = False
            mock_parse.return_value = (mock_q, None)

            query, tmpl = _get_matched_path_format(mock_obj, path_formats)
            assert query == PF_KEY_DEFAULT
            assert tmpl is default_tmpl

    def test_no_default_rule_raises_user_error(self):
        """Raises UserError when path_formats has no default rule."""
        mock_obj = MagicMock()
        mock_obj.__class__ = MagicMock()

        singleton_tmpl = template("Singles/$title")
        path_formats = [("singleton:true", singleton_tmpl)]

        with patch("beets.library.queries.parse_query_string") as mock_parse:
            mock_q = MagicMock()
            mock_q.match.return_value = False
            mock_parse.return_value = (mock_q, None)

            with pytest.raises(UserError) as excinfo:
                _get_matched_path_format(mock_obj, path_formats)
            assert "default path format" in str(excinfo.value).lower()
            assert "paths configuration" in str(excinfo.value).lower()

    def test_first_query_matches(self):
        """First matching query rule wins."""
        mock_obj = MagicMock()
        mock_obj.__class__ = MagicMock()

        default_tmpl = template("$artist/$album/$title")
        comp_tmpl = template("Compilations/$album/$title")
        singleton_tmpl = template("Singles/$title")
        path_formats = [
            ("comp:true", comp_tmpl),
            ("singleton:true", singleton_tmpl),
            (PF_KEY_DEFAULT, default_tmpl),
        ]

        with patch("beets.library.queries.parse_query_string") as mock_parse:
            mock_q1 = MagicMock()
            mock_q1.match.return_value = True
            mock_q2 = MagicMock()
            mock_q2.match.return_value = True
            mock_parse.side_effect = [(mock_q1, None), (mock_q2, None)]

            query, tmpl = _get_matched_path_format(mock_obj, path_formats)
            assert query == "comp:true"
            assert tmpl is comp_tmpl


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    import re

    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


class TestPreviewPaths:
    """Tests for preview_paths main function."""

    def test_empty_query_result(self, capfd):
        """Preview shows helpful message when query returns no items."""
        mock_lib = MagicMock()

        with patch("beets.ui.commands.utils.do_query") as mock_do_query:
            mock_do_query.return_value = ([], [])

            def dest_getter(obj):
                return b"/dest/file.mp3"

            preview_paths(mock_lib, [], False, dest_getter)

        captured = capfd.readouterr()
        assert "No matching items found" in captured.out
        assert "query" in captured.out.lower()

    def test_custom_empty_message(self, capfd):
        """Preview shows custom empty message when provided."""
        mock_lib = MagicMock()

        with patch("beets.ui.commands.utils.do_query") as mock_do_query:
            mock_do_query.return_value = ([], [])

            def dest_getter(obj):
                return b"/dest/file.mp3"

            preview_paths(
                mock_lib,
                [],
                False,
                dest_getter,
                empty_msg="No items to convert",
            )

        captured = capfd.readouterr()
        assert "No items to convert" in captured.out

    def test_preview_shows_paths(self, capfd):
        """Preview shows source and destination paths."""
        mock_lib = MagicMock()
        mock_item = MagicMock()
        mock_item.path = b"/source/file.mp3"
        mock_item.formatted.return_value = {
            "artist": "Beatles",
            "title": "Song",
        }

        with patch("beets.ui.commands.utils.do_query") as mock_do_query:
            mock_do_query.return_value = ([mock_item], [])

            with patch(
                "beets.util.preview._get_matched_path_format"
            ) as mock_get_format:
                mock_get_format.return_value = (
                    PF_KEY_DEFAULT,
                    template("$artist/$title"),
                )

                def dest_getter(obj):
                    return b"/dest/file.mp3"

                preview_paths(mock_lib, [], False, dest_getter)

        captured = capfd.readouterr()
        out = _strip_ansi(captured.out)
        assert "source" in out.lower()
        assert "dest" in out.lower()
        assert "rule:" in out

    def test_preview_detects_conflicts(self, capfd):
        """Preview detects and reports destination conflicts."""
        mock_lib = MagicMock()
        mock_item1 = MagicMock()
        mock_item1.path = b"/source/file1.mp3"
        mock_item1.formatted.return_value = {
            "artist": "Beatles",
            "title": "Song1",
        }
        mock_item2 = MagicMock()
        mock_item2.path = b"/source/file2.mp3"
        mock_item2.formatted.return_value = {
            "artist": "Beatles",
            "title": "Song2",
        }

        with patch("beets.ui.commands.utils.do_query") as mock_do_query:
            mock_do_query.return_value = ([mock_item1, mock_item2], [])

            with patch(
                "beets.util.preview._get_matched_path_format"
            ) as mock_get_format:
                mock_get_format.return_value = (
                    PF_KEY_DEFAULT,
                    template("$artist/$title"),
                )

                def dest_getter(obj):
                    return b"/dest/same_file.mp3"

                preview_paths(mock_lib, [], False, dest_getter)

        captured = capfd.readouterr()
        assert "CONFLICT" in captured.out
        assert "conflict" in captured.out.lower()

    def test_preview_detects_missing_fields(self, capfd):
        """Preview detects and reports missing/empty fields."""
        mock_lib = MagicMock()
        mock_item = MagicMock()
        mock_item.path = b"/source/file.mp3"
        mock_item.formatted.return_value = {"artist": "", "title": "Song"}

        with patch("beets.ui.commands.utils.do_query") as mock_do_query:
            mock_do_query.return_value = ([mock_item], [])

            with patch(
                "beets.util.preview._get_matched_path_format"
            ) as mock_get_format:
                mock_get_format.return_value = (
                    PF_KEY_DEFAULT,
                    template("$artist/$title"),
                )

                def dest_getter(obj):
                    return b"/dest/file.mp3"

                preview_paths(mock_lib, [], False, dest_getter)

        captured = capfd.readouterr()
        assert "MISSING" in captured.out
        assert "$artist" in captured.out

    def test_preview_album_mode(self, capfd):
        """Preview works in album mode."""
        mock_lib = MagicMock()
        mock_album = MagicMock()
        mock_item = MagicMock()
        mock_item.path = b"/source/file.mp3"
        mock_item.formatted.return_value = {
            "artist": "Beatles",
            "title": "Song",
        }
        mock_album.items.return_value = [mock_item]

        with patch("beets.ui.commands.utils.do_query") as mock_do_query:
            mock_do_query.return_value = ([], [mock_album])

            with patch(
                "beets.util.preview._get_matched_path_format"
            ) as mock_get_format:
                mock_get_format.return_value = (
                    PF_KEY_DEFAULT,
                    template("$artist/$title"),
                )

                def dest_getter(obj):
                    return b"/dest/file.mp3"

                preview_paths(mock_lib, [], True, dest_getter)

        captured = capfd.readouterr()
        out = _strip_ansi(captured.out)
        assert "album" in out.lower()
        assert "source" in out.lower()
