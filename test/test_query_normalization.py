# This file is part of beets.
# Copyright 2026.
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

"""Directed unit tests for the query normalization pipeline.

Covers the :class:`beets.library.QueryNormalizationContext` API surface
introduced to consolidate query construction across beets, including:

- :meth:`~QueryNormalizationContext.parse`  - str / list / tuple / Query
- :meth:`~QueryNormalizationContext.parse_sorted` - prefixes, paths, sorts
- :meth:`~QueryNormalizationContext.build_collection` - AndQuery / OrQuery
- :func:`~beets.library.get_query_prefixes` / :func:`normalize_query_parts`
- The :class:`DeprecationWarning` raised by the legacy
  :func:`beets.dbcore.query_from_strings` entry point.
"""

from __future__ import annotations

import os
import shlex
import tempfile
import warnings
from typing import ClassVar

import pytest

import beets.dbcore
import beets.dbcore.query as dbq
import beets.library
from beets import context, util
from beets.dbcore import AndQuery, OrQuery
from beets.dbcore.query import (
    InvalidQueryError,
    MatchQuery,
    NoneQuery,
    NotQuery,
    OrQuery as DbOrQuery,
    PathQuery,
    Query,
    RegexpQuery,
    SingletonQuery,
    StringQuery,
    SubstringQuery,
)
from beets.dbcore.sort import FixedFieldSort, MultipleSort, NullSort
from beets.library import (
    Album,
    Item,
    QueryNormalizationContext,
    build_query_context,
    get_query_prefixes,
    get_sort_case_insensitive,
    normalize_query_parts,
    parse_query_parts,
    parse_query_string,
)
from beets.test._common import item
from beets.test.helper import PytestTestHelper


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _item_matches(it: Item, q: Query) -> bool:
    return q.match(it)


def _make_item(**values):
    it = item(**values)
    return it


# =============================================================================
# 1. Configuration & primitive helpers
# =============================================================================


class TestQueryPrefixes:
    """Prefix map must contain the documented built-ins and be extensible."""

    def test_builtin_prefixes_present(self):
        prefixes = get_query_prefixes()
        assert prefixes[":"] is RegexpQuery
        assert prefixes["=~"] is StringQuery
        assert prefixes["="] is MatchQuery

    def test_prefixes_deterministic_and_mutable_copy(self):
        a = get_query_prefixes()
        b = get_query_prefixes()
        assert a == b
        a["__testing__"] = MatchQuery
        assert "__testing__" not in get_query_prefixes()


class TestSortCaseInsensitive(PytestTestHelper):
    def test_default_true(self):
        assert get_sort_case_insensitive() is True

    def test_follows_config(self):
        self.config["sort_case_insensitive"] = False
        # Re-read by constructing a new context
        ctx = build_query_context()
        assert ctx.case_insensitive is False


class TestNormalizeQueryParts:
    """``normalize_query_parts`` currently special-cases path tokens."""

    def test_plain_terms_pass_through(self):
        assert normalize_query_parts(["artist:beatles", "title:yesterday"]) == [
            "artist:beatles",
            "title:yesterday",
        ]

    def test_non_existent_path_not_transformed(self):
        parts = ["/this/path/does/not/exist-xyz.flac"]
        # No file exists → stays as-is (no path: prefix injected)
        assert normalize_query_parts(parts) == parts

    def test_existing_path_gets_path_prefix(self):
        with tempfile.NamedTemporaryFile(suffix=".mp3") as tmp:
            parts = [tmp.name]
            (result,) = normalize_query_parts(parts)
            assert result.startswith("path:")
            assert result.endswith(tmp.name)

    def test_explicit_path_query_not_double_wrapped(self):
        with tempfile.NamedTemporaryFile(suffix=".mp3") as tmp:
            parts = [f"path:{tmp.name}"]
            (result,) = normalize_query_parts(parts)
            # Already has path: → no second prefix
            assert result.count("path:") == 1


# =============================================================================
# 2. QueryNormalizationContext.parse — four input kinds
# =============================================================================


class TestParseStr(PytestTestHelper):
    """``parse(str, ...)`` splits via shlex and resolves sorts."""

    def test_plain_term_becomes_substring_match(self):
        ctx = QueryNormalizationContext()
        q, sort = ctx.parse("beatles", Item)
        # Single plain term gets wrapped: AndQuery([OrQuery([SubstringQuery...])])
        assert isinstance(q, AndQuery)
        inner_or = q.subqueries[0]
        assert isinstance(inner_or, DbOrQuery)
        # All sub-queries are SubstringQuery (any-field matching)
        assert all(isinstance(sq, SubstringQuery) for sq in inner_or.subqueries)
        assert isinstance(sort, NullSort)

    def test_field_term_and_sort_combined(self):
        ctx = QueryNormalizationContext()
        q, sort = ctx.parse("year:1990 year+", Item)
        assert isinstance(sort, FixedFieldSort)
        assert sort.field == "year"
        assert sort.ascending is True

    def test_quotes_preserved_through_shlex(self):
        ctx = QueryNormalizationContext()
        q, sort = ctx.parse('artist:"Pink Floyd"', Item)
        # Should build a single query (no trailing sort tokens)
        assert isinstance(q, Query)
        assert isinstance(sort, NullSort)

    def test_invalid_shlex_syntax_raises(self):
        ctx = QueryNormalizationContext()
        with pytest.raises(InvalidQueryError):
            ctx.parse('artist:"unterminated', Item)


class TestParseList(PytestTestHelper):
    def test_list_parts_equivalent_to_shlex_split(self):
        ctx = QueryNormalizationContext()
        q_list, _ = ctx.parse(["artist", "beatles"], Item)
        q_str, _ = ctx.parse("artist beatles", Item)
        # Same semantic: two OR-wrapped any_field subqueries joined by AND
        assert isinstance(q_list, AndQuery)
        assert isinstance(q_str, AndQuery)
        assert len(q_list.subqueries) == len(q_str.subqueries)

    def test_sort_tokens_recognized(self):
        ctx = QueryNormalizationContext()
        _, sort = ctx.parse(["artist:Radiohead", "title-"], Item)
        assert isinstance(sort, FixedFieldSort)
        assert sort.ascending is False


class TestParseTuple(PytestTestHelper):
    """Tuples behave exactly the same as lists for ``parse``."""

    def test_tuple_accepted(self):
        ctx = QueryNormalizationContext()
        q, sort = ctx.parse(("year:2000",), Album)
        assert isinstance(q, Query)
        assert isinstance(sort, NullSort)

    def test_tuple_matches_list_output(self):
        ctx = QueryNormalizationContext()
        q1, s1 = ctx.parse(["genre:Rock", "album+"], Album)
        q2, s2 = ctx.parse(("genre:Rock", "album+"), Album)
        assert type(q1) is type(q2)
        assert type(s1) is type(s2)


class TestParseQueryObject(PytestTestHelper):
    """Already-constructed Query objects are returned unchanged, sort=None."""

    def test_query_instance_passthrough(self):
        ctx = QueryNormalizationContext()
        original = MatchQuery("artist", "The Beatles")
        q, sort = ctx.parse(original, Item)
        assert q is original
        assert sort is None

    def test_notquery_passthrough(self):
        ctx = QueryNormalizationContext()
        original = NotQuery(NoneQuery("album_id"))
        q, sort = ctx.parse(original, Item)
        assert q is original
        assert sort is None


class TestParseUnsupportedType:
    def test_int_rejected(self):
        ctx = QueryNormalizationContext()
        with pytest.raises(TypeError, match="Unsupported query type"):
            ctx.parse(123, Item)  # type: ignore[arg-type]

    def test_none_rejected(self):
        ctx = QueryNormalizationContext()
        with pytest.raises(TypeError, match="Unsupported query type"):
            ctx.parse(None, Item)  # type: ignore[arg-type]

    def test_dict_rejected(self):
        ctx = QueryNormalizationContext()
        with pytest.raises(TypeError, match="Unsupported query type"):
            ctx.parse({"artist": "x"}, Item)  # type: ignore[arg-type]


# =============================================================================
# 3. build_collection — AndQuery / OrQuery construction via normalized path
# =============================================================================


class TestBuildCollection(PytestTestHelper):
    """Covered behaviours:

    * correct collection type used
    * prefixes (``:`` regex, ``=~`` string, ``=`` match) resolved
    * path normalization honoured
    * invalid argument values wrapped in ``InvalidQueryError``
    """

    def test_and_query_type(self):
        ctx = QueryNormalizationContext()
        q = ctx.build_collection(AndQuery, ["a", "b"], Item)
        assert isinstance(q, AndQuery)
        assert len(q.subqueries) == 2

    def test_or_query_type(self):
        ctx = QueryNormalizationContext()
        q = ctx.build_collection(OrQuery, ["x", "y", "z"], Album)
        assert isinstance(q, OrQuery)
        assert len(q.subqueries) == 3

    def test_empty_parts_yields_true_query(self):
        ctx = QueryNormalizationContext()
        q = ctx.build_collection(AndQuery, [], Item)
        # query_from_strings wraps missing parts with TrueQuery
        assert isinstance(q, AndQuery)
        assert len(q.subqueries) == 1

    def test_exact_prefix_match_resolved(self):
        ctx = QueryNormalizationContext()
        # Correct syntax: field:=value (colon + = prefix + exact value)
        q = ctx.build_collection(AndQuery, ["artist:=Beatles"], Item)
        # AND of a single field MatchQuery
        outer_and = q
        field_q = outer_and.subqueries[0]
        assert isinstance(field_q, MatchQuery)
        assert field_q.field_name == "artist"
        assert field_q.pattern == "Beatles"

    def test_regex_prefix_resolved(self):
        ctx = QueryNormalizationContext()
        # Correct syntax: field::pattern (colon + : prefix + regex pattern)
        q = ctx.build_collection(AndQuery, [r"title::^Love"], Item)
        outer_and = q
        field_q = outer_and.subqueries[0]
        assert isinstance(field_q, RegexpQuery)
        assert field_q.field_name == "title"

    def test_string_prefix_resolved(self):
        ctx = QueryNormalizationContext()
        # Correct syntax: field:=~pattern (colon + =~ prefix + value)
        q = ctx.build_collection(AndQuery, [r"album:=~Abbey Road"], Item)
        outer_and = q
        field_q = outer_and.subqueries[0]
        assert isinstance(field_q, StringQuery)
        assert field_q.field_name == "album"

    def test_path_normalization_applied(self):
        with tempfile.NamedTemporaryFile(suffix=".flac") as tmp:
            ctx = QueryNormalizationContext()
            q = ctx.build_collection(AndQuery, [tmp.name], Item)
            outer_and = q
            # Path query is normalized to path:<path> → constructs PathQuery directly
            field_q = outer_and.subqueries[0]
            assert isinstance(field_q, PathQuery)

    def test_numeric_invalid_wraps_error(self):
        ctx = QueryNormalizationContext()
        with pytest.raises(InvalidQueryError):
            # "year" expects numeric; "abc" → InvalidQueryArgumentValueError
            # which the context wraps to InvalidQueryError
            ctx.build_collection(AndQuery, ["year:abc"], Item)

    def test_date_invalid_wraps_error(self):
        ctx = QueryNormalizationContext()
        with pytest.raises(InvalidQueryError):
            # added is a DateField; "not-a-date" triggers a wrapped error
            ctx.build_collection(AndQuery, ["added:not-a-date"], Album)


# =============================================================================
# 4. parse_sorted — sort tokens, negation, comma-separated OR branches
# =============================================================================


class TestParseSortedSuccess(PytestTestHelper):
    """Positive cases for parse_sorted + ``parse_sorted``-level features."""

    def test_no_sort_returns_null_sort(self):
        ctx = QueryNormalizationContext()
        _, sort = ctx.parse_sorted(["title:yesterday"], Item)
        assert isinstance(sort, NullSort)

    def test_single_ascending_sort(self):
        ctx = QueryNormalizationContext()
        _, sort = ctx.parse_sorted(["title+", "year:2000"], Item)
        assert isinstance(sort, FixedFieldSort)
        assert sort.field == "title"
        assert sort.ascending is True

    def test_single_descending_sort(self):
        ctx = QueryNormalizationContext()
        _, sort = ctx.parse_sorted(["track-"], Item)
        assert isinstance(sort, FixedFieldSort)
        assert sort.field == "track"
        assert sort.ascending is False

    def test_multiple_sorts(self):
        ctx = QueryNormalizationContext()
        _, sort = ctx.parse_sorted(["album+", "track-"], Item)
        assert isinstance(sort, MultipleSort)

    def test_negation_prefix(self):
        ctx = QueryNormalizationContext()
        q, _ = ctx.parse_sorted(["-genre:Rock"], Item)
        it_rock = _make_item(genres=["Rock"])
        it_jazz = _make_item(genres=["Jazz"])
        # Negated genre → rocks excluded, jazz included
        assert not _item_matches(it_rock, q)
        assert _item_matches(it_jazz, q)

    def test_caret_negation_prefix(self):
        ctx = QueryNormalizationContext()
        q, _ = ctx.parse_sorted(["^comp:true"], Item)
        it_comp = _make_item(comp=True)
        it_sing = _make_item(comp=False)
        assert not _item_matches(it_comp, q)
        assert _item_matches(it_sing, q)

    def test_or_branch_with_comma(self):
        ctx = QueryNormalizationContext()
        # "year:2000, year:2010" is an OR of two AND subqueries
        q, _ = ctx.parse_sorted(["year:2000", ",", "year:2010"], Item)
        assert isinstance(q, DbOrQuery)
        it_2000 = _make_item(year=2000)
        it_2010 = _make_item(year=2010)
        it_2020 = _make_item(year=2020)
        assert _item_matches(it_2000, q)
        assert _item_matches(it_2010, q)
        assert not _item_matches(it_2020, q)

    def test_singleton_named_query(self):
        ctx = QueryNormalizationContext()
        q, _ = ctx.parse_sorted(["singleton:true"], Item)
        # parse_sorted wraps parts in an AndQuery; SingletonQuery.__new__
        # converts to NoneQuery('album_id') via field_query (fast=True).
        assert isinstance(q, AndQuery)
        assert len(q.subqueries) == 1
        inner = q.subqueries[0]
        assert isinstance(inner, NoneQuery)
        assert inner.field_name == "album_id"
        it_singleton = _make_item(album_id=None)
        it_in_album = _make_item(album_id=42)
        assert _item_matches(it_singleton, q)
        assert not _item_matches(it_in_album, q)


class TestParseSortedFailure(PytestTestHelper):
    """Error paths for parse_sorted."""

    def test_invalid_numeric_field_raises(self):
        ctx = QueryNormalizationContext()
        with pytest.raises(InvalidQueryError):
            ctx.parse_sorted(["bpm:not-a-number"], Item)

    def test_invalid_regex_raises(self):
        ctx = QueryNormalizationContext()
        with pytest.raises(InvalidQueryError):
            # Use a genuinely invalid regex: unclosed group
            ctx.parse_sorted([r"title::(?unclosed-group"], Item)

    def test_invalid_duration_raises(self):
        ctx = QueryNormalizationContext()
        with pytest.raises(InvalidQueryError):
            # length is a Duration field; can't parse "abc"
            ctx.parse_sorted(["length:abc"], Item)


# =============================================================================
# 5. DeprecationWarning for the legacy dbcore.query_from_strings entry point
# =============================================================================


class TestLegacyQueryFromStringsDeprecation:
    def test_warns_deprecation(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            beets.dbcore.query_from_strings(
                AndQuery, Item, prefixes={}, query_parts=["foo"]
            )
        deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert deprecations, "Expected at least one DeprecationWarning"
        assert "QueryNormalizationContext.build_collection" in str(
            deprecations[0].message
        )

    def test_still_returns_correct_type(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            q = beets.dbcore.query_from_strings(
                OrQuery, Album, prefixes={}, query_parts=[]
            )
        assert isinstance(q, OrQuery)


# =============================================================================
# 6. Backwards compatibility of the top-level helper functions that have
#    existed before the refactor. The old entry points must still behave
#    exactly as callers expect.
# =============================================================================


class TestTopLevelBackwardsCompat(PytestTestHelper):
    def test_parse_query_string_bytes_assert(self):
        # Old test: non-unicode input triggers AssertionError
        with pytest.raises(AssertionError):
            parse_query_string(b"query", None)  # type: ignore[arg-type]

    def test_parse_query_string_shlex_error_wrapped(self):
        with pytest.raises(InvalidQueryError):
            parse_query_string('foo"', Item)

    def test_parse_query_parts_equivalent(self):
        parts = ["artist:Beatles", "year+"]
        q1, s1 = parse_query_parts(parts, Item)
        ctx = QueryNormalizationContext()
        q2, s2 = ctx.parse_sorted(parts, Item)
        assert type(q1) is type(q2)
        assert type(s1) is type(s2)


# =============================================================================
# 7. build_query_context factory — override knobs
# =============================================================================


class TestBuildQueryContext:
    def test_defaults_equivalent_to_direct_context(self):
        baseline = QueryNormalizationContext()
        custom = build_query_context()
        assert baseline.prefixes == custom.prefixes
        assert baseline.case_insensitive == custom.case_insensitive
        assert (
            baseline.apply_parts_normalization
            == custom.apply_parts_normalization
        )

    def test_extra_prefixes_merged(self):
        class _FakeQuery(SubstringQuery):
            pass

        ctx = build_query_context(extra_prefixes={"#": _FakeQuery})
        assert ctx.prefixes["#"] is _FakeQuery
        # Built-ins still present
        assert ctx.prefixes[":"] is RegexpQuery

    def test_case_insensitive_override(self):
        ctx = build_query_context(case_insensitive=False)
        assert ctx.case_insensitive is False

    def test_disable_normalization(self):
        with tempfile.NamedTemporaryFile(suffix=".mp3") as tmp:
            ctx = build_query_context(apply_normalization=False)
            parts = [tmp.name]
            # With normalization disabled, no path: injection happens
            assert ctx.normalize(parts) == parts
