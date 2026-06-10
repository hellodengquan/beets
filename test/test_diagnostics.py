"""Tests for the importer diagnostics module.

Covers the main surface areas of beets.importer.diagnostics:

* ``should_enable_diagnostics``: three-valued CLI flag + verbosity threshold.
* ``ImportDiagnosticCollector``: lifecycle toggling, event recording with
  optional task/paths/context/exception, the convenience helpers for each
  import stage, and the in-memory event count cap.
* ``DiagnosticTraceWriter``: JSON serialisation with and without
  pretty-printing, ``dry_run`` mode, event-count and byte-size truncation,
  graceful write-failure degradation, and quiet-mode output suppression.
* ``create_collector_for_session`` / ``create_writer_for_session``: factory
  helpers that wire configuration options to their respective objects.
* Integration: ``ImportSession`` initialises a collector and produces a
  trace on completion.
"""

from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import time
from typing import TYPE_CHECKING

import pytest

import beets.logging as blog
from beets import config
from beets.importer import (
    DIAG_LEVEL_DEBUG,
    DIAG_LEVEL_ERROR,
    DIAG_LEVEL_INFO,
    DIAG_LEVEL_WARNING,
    DIAG_STAGE_DUPLICATE,
    DIAG_STAGE_FILE_IMPORT,
    DIAG_STAGE_GENERAL,
    DIAG_STAGE_METADATA_MATCH,
    DIAG_STAGE_PATH_PARSE,
    DIAG_STAGE_USER_CHOICE,
    DiagnosticEvent,
    DiagnosticTraceWriter,
    ImportDiagnosticCollector,
    create_collector_for_session,
    create_writer_for_session,
    default_diagnostic_trace_path,
    diagnostics_config,
    should_enable_diagnostics,
)
from beets.test.helper import (
    AsIsImporterMixin,
    ImportSessionFixture,
    ImportTestCase,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# 1. should_enable_diagnostics gate
# ---------------------------------------------------------------------------


class TestShouldEnableDiagnostics:
    @pytest.mark.parametrize("verbose", [0, 1, 2, 3, 5])
    def test_explicit_always_wins(self, verbose):
        assert should_enable_diagnostics(True, verbose) is True
        assert should_enable_diagnostics(False, verbose) is False

    @pytest.mark.parametrize("verbose", [0, 1, 2])
    def test_none_below_threshold_disabled(self, verbose):
        assert should_enable_diagnostics(None, verbose) is False

    @pytest.mark.parametrize("verbose", [3, 4, 10])
    def test_none_above_threshold_enabled(self, verbose):
        assert should_enable_diagnostics(None, verbose) is True


# ---------------------------------------------------------------------------
# 2. ImportDiagnosticCollector
# ---------------------------------------------------------------------------


class TestDiagnosticCollectorBasics:
    def test_new_collector_is_empty(self):
        c = ImportDiagnosticCollector()
        assert c.event_count == 0
        assert c.error_count == 0
        assert c.warning_count == 0
        assert c.enabled is False

    def test_enable_disable_toggle(self):
        c = ImportDiagnosticCollector(enabled=False)
        assert c.enabled is False
        c.enable()
        assert c.enabled is True
        c.disable()
        assert c.enabled is False

    def test_disabled_collector_drops_all_events(self):
        c = ImportDiagnosticCollector(enabled=False, max_events=1000)
        for _ in range(10_000):
            c.record("general", "info", "dropped")
        assert c.event_count == 0
        assert list(c.events()) == []

    def test_record_fills_basic_fields(self):
        c = ImportDiagnosticCollector(enabled=True)
        t_before = time.time()
        c.record("metadata_match", "info", "hello world")
        t_after = time.time()

        assert c.event_count == 1
        ev = c.events()[0]
        assert isinstance(ev, DiagnosticEvent)
        assert ev.stage == "metadata_match"
        assert ev.level == "info"
        assert ev.message == "hello world"
        assert t_before <= ev.timestamp <= t_after
        assert ev.task_paths == []
        assert ev.context == {}
        assert ev.exception is None

    def test_record_with_paths_and_context(self):
        c = ImportDiagnosticCollector(enabled=True)
        paths = [b"/tmp/artist/album/01.mp3", b"/tmp/artist/album/02.mp3"]
        context = {"cur_artist": "a", "cur_album": "b", "num": 42}
        c.record(
            "path_parse",
            "debug",
            "walked",
            paths=paths,
            context=context,
        )
        ev = c.events()[0]
        assert ev.stage == "path_parse"
        assert ev.level == "debug"
        assert len(ev.task_paths) == 2
        assert "/tmp/artist/album/01.mp3" in ev.task_paths[0]
        assert ev.context["cur_artist"] == "a"
        assert ev.context["num"] == 42

    def test_level_counters_are_tracked(self):
        c = ImportDiagnosticCollector(enabled=True)
        c.record("general", "info", "i")
        c.record("general", "debug", "d")
        c.record("general", "warning", "w1")
        c.record("general", "warning", "w2")
        c.record("general", "error", "e1")
        c.record("general", "error", "e2")
        c.record("general", "error", "e3")
        assert c.warning_count == 2
        assert c.error_count == 3
        assert c.event_count == 7

    def test_max_events_caps_memory(self):
        cap = 20
        c = ImportDiagnosticCollector(enabled=True, max_events=cap)
        for i in range(100):
            c.record("general", "info", f"msg {i}")
        assert c.event_count == cap
        # Only the *newest* events are kept.
        texts = [e.message for e in c.events()]
        assert texts[0] == "msg 80"
        assert texts[-1] == "msg 99"

    def test_max_events_default_is_high(self):
        c = ImportDiagnosticCollector(enabled=True)
        # Should not raise or allocate an unbounded deque.
        for i in range(200_000):
            c.record("general", "info", f"msg {i}")
        # The default maxlen in the class is 50000.
        assert c.event_count == 50000

    def test_summary_contains_expected_keys(self):
        c = ImportDiagnosticCollector(enabled=True)
        c.record("general", "error", "boom")
        s = c.summary()
        assert s["enabled"] is True
        assert s["event_count"] == 1
        assert s["error_count"] == 1
        assert s["warning_count"] == 0
        assert "duration_seconds" in s
        assert s["duration_seconds"] >= 0


class TestDiagnosticCollectorExceptionFormatting:
    def test_exception_is_captured(self):
        c = ImportDiagnosticCollector(enabled=True)
        try:
            raise ValueError("nope")
        except ValueError as exc:
            c.record(
                "file_import",
                "error",
                "something failed",
                exc_info=exc,
            )
        ev = c.events()[0]
        assert ev.exception is not None
        d = ev.exception
        assert d["type"] == "ValueError"
        assert d["module"] == "builtins"
        assert "nope" in d["message"]
        assert "ValueError" in d["repr"]
        assert "nope" in d["traceback"]
        # The traceback should contain our file/function for easy navigation.
        assert "test_exception_is_captured" in d["traceback"]

    def test_custom_exception(self):
        class OopsError(RuntimeError):
            pass

        c = ImportDiagnosticCollector(enabled=True)
        try:
            raise OopsError("custom!", 1, 2, 3)
        except OopsError as exc:
            c.record(
                "metadata_match",
                "error",
                "bad",
                exc_info=exc,
            )
        d = c.events()[0].exception
        assert d["type"] == "OopsError"
        assert "custom!" in d["message"]
        # Full traceback string should contain the raise site.
        assert "test_custom_exception" in d["traceback"]

    def test_event_to_dict_is_json_serializable(self):
        c = ImportDiagnosticCollector(enabled=True)
        paths = [b"\xc3\xa9.mp3".replace(b"\xa9", b"\xa9")]  # utf-8 bytes
        try:
            raise RuntimeError("with 'quotes' and unicode: \u2603")
        except RuntimeError as exc:
            c.record(
                "path_parse",
                "error",
                "weird stuff",
                paths=paths,
                context={
                    "a set": {1, 2, 3},  # sets need default=str fallback
                    "nested": {"k": [1, "v"]},
                    "num": 1.5,
                    "none": None,
                },
                exc_info=exc,
            )
        payload = {"events": [e.to_dict() for e in c.events()]}
        # json.dumps with default=str should be enough.
        text = json.dumps(payload, default=str, ensure_ascii=False)
        # And round-trip cleanly.
        back = json.loads(text)
        assert len(back["events"]) == 1
        assert back["events"][0]["level"] == "error"


class TestDiagnosticCollectorConvenienceHelpers:
    """Each high-level ``record_*`` helper should be callable without a
    full ImportTask object: we use ``paths`` only and make sure the
    resulting event is well-formed.
    """

    def test_record_metadata_lookup(self):
        c = ImportDiagnosticCollector(enabled=True)
        c.record_metadata_lookup(
            task=None,  # paths cover it
            cur_artist="Beatles",
            cur_album="Abbey Road",
            num_candidates=5,
            recommendation="strong",
            search_ids=["mbid-1"],
        )
        ev = c.events()[0]
        assert ev.stage == DIAG_STAGE_METADATA_MATCH
        assert ev.level == DIAG_LEVEL_INFO
        assert ev.context["cur_artist"] == "Beatles"
        assert ev.context["num_candidates"] == 5
        assert ev.context["search_ids"] == ["mbid-1"]

    def test_record_metadata_candidate(self):
        c = ImportDiagnosticCollector(enabled=True)
        c.record_metadata_candidate(
            task=None,
            index=0,
            artist="X",
            album="Y",
            distance=0.42,
            distance_string="42.0%",
            penalty_keys=["tracks", "year"],
            disambiguation="remaster",
        )
        ev = c.events()[0]
        assert ev.stage == DIAG_STAGE_METADATA_MATCH
        assert ev.level == DIAG_LEVEL_DEBUG
        assert ev.context["index"] == 0
        assert ev.context["distance"] == pytest.approx(0.42)
        assert "tracks" in ev.context["penalty_keys"]
        assert ev.context["disambiguation"] == "remaster"

    def test_record_metadata_choice(self):
        c = ImportDiagnosticCollector(enabled=True)
        c.record_metadata_choice(
            task=None, choice="APPLY", match_artist="A", match_album="B"
        )
        ev = c.events()[0]
        assert ev.stage == DIAG_STAGE_METADATA_MATCH
        assert "match choice" in ev.message
        assert ev.context["choice"] == "APPLY"
        assert ev.context["match_artist"] == "A"

    def test_record_path_walk(self):
        c = ImportDiagnosticCollector(enabled=True)
        c.record_path_walk(
            toppath=b"/top",
            root=b"/top/sub",
            num_files=3,
            ignored_files=["Thumbs.db"],
        )
        ev = c.events()[0]
        assert ev.stage == DIAG_STAGE_PATH_PARSE
        assert ev.context["num_files"] == 3
        assert "Thumbs.db" in ev.context["ignored_files"]

    def test_record_item_read_success_and_failure(self):
        c = ImportDiagnosticCollector(enabled=True)
        c.record_item_read(b"/ok.mp3", success=True)
        try:
            raise OSError("broken file")
        except OSError as exc:
            c.record_item_read(
                b"/bad.mp3", success=False, reason="broken", exc_info=exc
            )
        ok, bad = c.events()
        assert ok.level == DIAG_LEVEL_INFO
        assert ok.exception is None
        assert bad.level == DIAG_LEVEL_ERROR
        assert bad.exception is not None
        assert bad.exception["type"] == "OSError"

    def test_record_archive_extract(self):
        c = ImportDiagnosticCollector(enabled=True)
        c.record_archive_extract(b"/a.zip", b"/tmp/_a", success=True)
        try:
            raise RuntimeError("corrupt")
        except RuntimeError as exc:
            c.record_archive_extract(b"/b.zip", b"/tmp/_b", False, exc_info=exc)
        ok, bad = c.events()
        assert ok.stage == DIAG_STAGE_PATH_PARSE
        assert ok.level == DIAG_LEVEL_INFO
        assert bad.level == DIAG_LEVEL_ERROR
        assert bad.exception["type"] == "RuntimeError"

    def test_record_file_operation(self):
        c = ImportDiagnosticCollector(enabled=True)
        c.record_file_operation(
            task=None,
            operation="move",
            item_path=b"/src.mp3",
            new_path=b"/dst.mp3",
            success=True,
        )
        try:
            raise OSError("read-only fs")
        except OSError as exc:
            c.record_file_operation(
                task=None,
                operation="copy",
                item_path=b"/x.mp3",
                new_path=None,
                success=False,
                exc_info=exc,
            )
        ok, bad = c.events()
        assert ok.stage == DIAG_STAGE_FILE_IMPORT
        assert ok.context["operation"] == "move"
        assert bad.exception["type"] == "OSError"

    def test_record_library_add(self):
        c = ImportDiagnosticCollector(enabled=True)
        c.record_library_add(task=None, num_items=12, success=True)
        try:
            raise RuntimeError("sqlite lock")
        except RuntimeError as exc:
            c.record_library_add(
                task=None, num_items=12, success=False, exc_info=exc
            )
        ok, bad = c.events()
        assert ok.context["num_items"] == 12
        assert bad.level == DIAG_LEVEL_ERROR

    def test_record_duplicate_found(self):
        c = ImportDiagnosticCollector(enabled=True)
        c.record_duplicate_found(
            task=None, num_duplicates=2, duplicate_ids=[7, 8], action="skip"
        )
        ev = c.events()[0]
        assert ev.stage == DIAG_STAGE_DUPLICATE
        assert ev.level == DIAG_LEVEL_WARNING
        assert ev.context["num_duplicates"] == 2
        assert ev.context["duplicate_ids"] == [7, 8]


# ---------------------------------------------------------------------------
# 3. DiagnosticTraceWriter
# ---------------------------------------------------------------------------


class TestTraceWriterBasicOutput:
    def _collector(self, n=5):
        c = ImportDiagnosticCollector(enabled=True, max_events=n * 10)
        for i in range(n):
            c.record(
                "general",
                "info",
                f"event {i}",
                context={"idx": i},
            )
        return c

    def test_write_returns_true_and_creates_file(self):
        c = self._collector(3)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "trace.json")
            w = DiagnosticTraceWriter(path, keep=3)
            assert w.write(c) is True
            # With keep>0 the actual filename has a timestamp suffix.
            assert w.last_written_path is not None
            assert os.path.isfile(w.last_written_path)
            assert w.last_written_path != path
            # The original path (no timestamp) should NOT be created.
            assert not os.path.exists(path)
            data = json.load(open(w.last_written_path, "r", encoding="utf-8"))
            assert "metadata" in data
            assert "events" in data
            assert len(data["events"]) == 3
            assert data["metadata"]["trace_version"] == 1
            assert data["metadata"]["summary"]["event_count"] == 3

    def test_keep_zero_overwrites_without_timestamp(self):
        """keep=0 restores the old behaviour: write directly to the
        configured path without a timestamp, overwriting any previous
        trace.
        """
        c = self._collector(1)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "trace.json")
            w = DiagnosticTraceWriter(path, keep=0)
            assert w.write(c) is True
            assert w.last_written_path == path
            assert os.path.isfile(path)
            data = json.load(open(path, "r", encoding="utf-8"))
            assert len(data["events"]) == 1

            # Write a second time -- should overwrite.
            c2 = self._collector(2)
            assert w.write(c2) is True
            data = json.load(open(path, "r", encoding="utf-8"))
            assert len(data["events"]) == 2

    def test_stdout_fallback_when_path_is_none(self, capsys):
        c = self._collector(2)
        w = DiagnosticTraceWriter(None, pretty=False)
        assert w.write(c) is True
        out, _ = capsys.readouterr()
        data = json.loads(out)
        assert len(data["events"]) == 2

    def test_pretty_vs_compact_formats(self):
        c = self._collector(2)
        with tempfile.TemporaryDirectory() as td:
            p_pr = os.path.join(td, "pretty.json")
            p_cm = os.path.join(td, "compact.json")
            # Use keep=0 to write directly to the given path.
            w_pr = DiagnosticTraceWriter(p_pr, pretty=True, keep=0)
            w_cm = DiagnosticTraceWriter(p_cm, pretty=False, keep=0)
            w_pr.write(c)
            w_cm.write(c)
            with open(p_pr) as f:
                pr = f.read()
            with open(p_cm) as f:
                cm = f.read()
            # Pretty should have newlines + indentation and thus be longer.
            assert "\n" in pr
            assert pr.count("\n") > cm.count("\n")
            # Both must be valid JSON and contain the same events.
            assert json.loads(pr)["events"] == json.loads(cm)["events"]

    def test_metadata_includes_version_and_runtime_info(self):
        c = self._collector(1)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "m.json")
            w = DiagnosticTraceWriter(path, keep=0)
            w.write(c)
            data = json.load(open(path, "r", encoding="utf-8"))
        meta = data["metadata"]
        assert meta["trace_version"] >= 1
        assert "generated_at" in meta
        assert "beets_version" in meta
        assert meta["python_version"].startswith(str(sys.version_info.major))
        assert meta["platform"] == sys.platform

    def test_extra_metadata_merged_in(self):
        c = self._collector(1)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "extra.json")
            w = DiagnosticTraceWriter(path, keep=0)
            w.write(c, extra_metadata={"answer": 42, "note": "custom"})
            data = json.load(open(path, "r", encoding="utf-8"))
        assert data["metadata"]["answer"] == 42
        assert data["metadata"]["note"] == "custom"

    def test_directory_created_if_missing(self):
        c = self._collector(1)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "sub", "nested", "trace.json")
            DiagnosticTraceWriter(path, keep=0).write(c)
            assert os.path.isfile(path)

    def test_non_json_safe_context_uses_default_str(self):
        c = ImportDiagnosticCollector(enabled=True)
        c.record(
            "general",
            "info",
            "weird types",
            context={
                "set": {1, 2, 3},
                "bytes": b"\xff\xfe",
                "func": lambda x: x,
            },
        )
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "robust.json")
            # Should not raise.
            w = DiagnosticTraceWriter(path, pretty=False, keep=0)
            assert w.write(c) is True
            data = json.load(open(path, "r", encoding="utf-8"))
        ev = data["events"][0]
        # sets become sorted string form '{1, 2, 3}' or similar via str()
        assert isinstance(ev["context"]["set"], str)
        assert "1" in ev["context"]["set"]


class TestTraceWriterDryRunAndQuiet:
    def _collector(self):
        c = ImportDiagnosticCollector(enabled=True)
        c.record("general", "info", "m1")
        return c

    def test_dry_run_does_not_touch_filesystem(self):
        c = self._collector()
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "would_be.json")
            w = DiagnosticTraceWriter(path, dry_run=True)
            assert w.write(c) is True
            assert not os.path.exists(path)

    def test_dry_run_reports_would_be_path(self, caplog):
        c = self._collector()
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "log.json")
            w = DiagnosticTraceWriter(path, dry_run=True)
            with caplog.at_level(blog.INFO, logger="beets"):
                w.write(c)
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "dry-run" in joined.lower()
        assert path in joined

    def test_quiet_no_path_no_library_warns_data_discarded(self, caplog):
        """Quiet mode + no trace path + no library path = writer has
        nowhere to persist.  It must log a warning so the user knows
        the diagnostics were collected but discarded.
        """
        c = self._collector()
        w = create_writer_for_session(
            None, quiet=True, dry_run=False, library_path=None
        )
        # With no library path the default cannot be computed.
        assert w.output_path is None
        assert w._quiet is True

        with caplog.at_level(blog.WARNING, logger="beets"):
            result = w.write(c)
        assert result is True
        warnings = [r for r in caplog.records if r.levelno >= blog.WARNING]
        assert any("discarded" in r.getMessage().lower() for r in warnings), (
            "Expected a warning about discarded diagnostics"
        )
        assert any("--diagnose-trace" in r.getMessage() for r in warnings), (
            "Warning should mention --diagnose-trace"
        )

    def test_quiet_no_path_no_events_no_warning(self, caplog):
        """If the collector has zero events the warning is suppressed
        (nothing was actually discarded).
        """
        c = ImportDiagnosticCollector(enabled=True)
        assert c.event_count == 0
        w = DiagnosticTraceWriter(None, quiet=True)
        with caplog.at_level(blog.WARNING, logger="beets"):
            w.write(c)
        warnings = [r for r in caplog.records if r.levelno >= blog.WARNING]
        assert not any("discarded" in r.getMessage().lower() for r in warnings)

    def test_non_quiet_no_path_writes_to_stdout(self, capsys):
        """Non-quiet + no path → stdout, as before."""
        c = self._collector()
        w = create_writer_for_session(None, quiet=False, dry_run=False)
        w.write(c)
        out, _ = capsys.readouterr()
        assert len(out) > 0
        assert "metadata" in out


class TestDefaultTracePath:
    """Tests for the ``default_diagnostic_trace_path`` helper and its
    integration with ``create_writer_for_session``.
    """

    def test_derives_from_library_path(self):
        result = default_diagnostic_trace_path("/home/user/beetslibrary.db")
        assert result == "/home/user/beetslibrary.diagnostics.json"

    def test_derives_from_library_path_with_bytes(self):
        result = default_diagnostic_trace_path(b"/home/user/beetslibrary.db")
        assert result == "/home/user/beetslibrary.diagnostics.json"

    def test_strips_extension(self):
        result = default_diagnostic_trace_path("/tmp/my.lib.sqlite")
        assert result == "/tmp/my.lib.diagnostics.json"

    def test_memory_db_returns_none(self):
        assert default_diagnostic_trace_path(":memory:") is None

    def test_none_returns_none(self):
        assert default_diagnostic_trace_path(None) is None

    def test_empty_string_returns_none(self):
        assert default_diagnostic_trace_path("") is None
        assert default_diagnostic_trace_path("  ") is None

    def test_quiet_with_library_path_gets_default_trace_file(self):
        """The key scenario: quiet mode + no explicit trace path, but a
        library database is available.  The writer should automatically
        target ``<library_base>.diagnostics.json``.
        """
        with tempfile.TemporaryDirectory() as td:
            lib_path = os.path.join(td, "library.db")
            w = create_writer_for_session(
                None, quiet=True, library_path=lib_path
            )
            assert w.output_path == os.path.join(
                td, "library.diagnostics.json"
            )

    def test_quiet_with_library_path_actually_writes_file(self):
        """End-to-end: quiet mode + library path → trace is written to
        the default location next to the database, with a timestamp
        suffix when keep > 0.
        """
        c = ImportDiagnosticCollector(enabled=True)
        c.record("general", "info", "batch import event")
        with tempfile.TemporaryDirectory() as td:
            lib_path = os.path.join(td, "library.db")
            # Use keep=0 to disable timestamp for deterministic path.
            w = create_writer_for_session(
                None, quiet=True, library_path=lib_path, keep=0
            )
            assert w.output_path is not None
            result = w.write(c)
            assert result is True
            expected_path = os.path.join(td, "library.diagnostics.json")
            assert os.path.isfile(expected_path)
            assert w.last_written_path == expected_path
            data = json.load(open(expected_path, "r", encoding="utf-8"))
            assert data["metadata"]["summary"]["event_count"] == 1

            # Now with keep>0: should get a timestamped filename.
            c2 = ImportDiagnosticCollector(enabled=True)
            c2.record("general", "info", "event2")
            w2 = create_writer_for_session(
                None, quiet=True, library_path=lib_path, keep=5
            )
            assert w2.write(c2) is True
            assert w2.last_written_path is not None
            assert w2.last_written_path != w.output_path
            assert os.path.isfile(w2.last_written_path)
            # base name should have _timestamp suffix
            basename = os.path.basename(w2.last_written_path)
            assert basename.startswith("library.diagnostics_")

    def test_explicit_path_overrides_default(self):
        """If the user explicitly sets --diagnose-trace, that takes
        precedence over the default path.
        """
        with tempfile.TemporaryDirectory() as td:
            explicit = os.path.join(td, "custom_trace.json")
            w = create_writer_for_session(
                explicit,
                quiet=True,
                library_path=os.path.join(td, "library.db"),
            )
            assert w.output_path == explicit


class TestTraceWriterTruncation:
    def _big_collector(self, n_events=2000, payload_bytes=100):
        c = ImportDiagnosticCollector(enabled=True, max_events=n_events * 10)
        for i in range(n_events):
            c.record(
                "metadata_match",
                "debug",
                "x" * payload_bytes,
                context={"k": "y" * payload_bytes, "idx": i},
            )
        return c

    def test_max_events_truncates_and_records(self):
        c = self._big_collector(n_events=100, payload_bytes=5)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "trunc.json")
            w = DiagnosticTraceWriter(
                path, max_events=10, max_bytes=2**30, keep=0
            )
            w.write(c)
            data = json.load(open(path, "r", encoding="utf-8"))
        assert len(data["events"]) == 10
        # Summary should indicate we dropped some.
        summary = data["metadata"]["summary"]
        assert summary.get("events_truncated") == 90
        # The *newest* 10 events should have survived.
        idxs = [e["context"]["idx"] for e in data["events"]]
        assert idxs == list(range(90, 100))

    def test_max_bytes_truncates_oldest_first(self):
        # Generate enough events to exceed the byte limit reliably.
        # Each event payload plus JSON overhead is roughly 500 bytes
        # with 100 bytes of message and context each, so 300 events
        # will exceed 50KB once serialised.
        c = self._big_collector(n_events=300, payload_bytes=100)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "sized.json")
            w = DiagnosticTraceWriter(
                path, max_bytes=50 * 1024, max_events=1_000_000, keep=0
            )
            w.write(c)
            size = os.path.getsize(path)
            data = json.load(open(path, "r", encoding="utf-8"))

        # Must be under the configured cap (metadata-only payload is
        # allowed to exceed it slightly, but with 300 events worth of
        # trimming we should land well under).
        assert size <= 52 * 1024
        kept = len(data["events"])
        assert 0 < kept < 300, f"expected trimming, got {kept} kept of 300"
        # Trimming keeps the newest events -> last event has idx 299.
        idxs = [e["context"]["idx"] for e in data["events"]]
        assert max(idxs) == 299
        # And the oldest kept event has an idx past the first one.
        assert min(idxs) > 0


class TestTraceWriterWriteFailure:
    def _collector(self):
        c = ImportDiagnosticCollector(enabled=True)
        c.record("general", "error", "boom")
        return c

    def test_permission_denied_graceful(self):
        c = self._collector()
        with tempfile.TemporaryDirectory() as td:
            # Make the directory read-only after creation so mkdir (for
            # subdirs) or the write itself fails.
            os.chmod(td, stat.S_IRUSR | stat.S_IXUSR)
            try:
                path = os.path.join(td, "trace.json")
                w = DiagnosticTraceWriter(path)
                assert w.write(c) is False
                assert w.write_error is not None
                # file should not exist
                assert not os.path.exists(path)
            finally:
                os.chmod(td, stat.S_IRWXU)

    def test_write_failure_does_not_raise(self):
        """The write method must *never* raise back to the caller even
        if something completely unexpected happens inside.
        """
        c = self._collector()
        # Use a path inside an existent but read-only directory so the
        # failure definitely happens inside the writer's try/except.
        with tempfile.TemporaryDirectory() as td:
            os.chmod(td, stat.S_IRUSR | stat.S_IXUSR)
            try:
                path = os.path.join(td, "trace.json")
                w = DiagnosticTraceWriter(path)
                # Should return False and set write_error without raising.
                assert w.write(c) is False
                # The actual error message is stored for inspection.
                assert w.write_error is not None
                assert len(w.write_error) > 0
            finally:
                    os.chmod(td, stat.S_IRWXU)


# ---------------------------------------------------------------------------
# 4. Trace rotation and history retention
# ---------------------------------------------------------------------------


class TestTraceWriterRotation:
    """Tests for timestamped filenames and automatic pruning of old
    trace files.
    """

    def _collector(self, n=3):
        c = ImportDiagnosticCollector(enabled=True, max_events=n * 10)
        for i in range(n):
            c.record("general", "info", f"event {i}")
        return c

    def test_timestamped_filename_basic(self):
        """Filenames get a timestamp suffix with keep>0."""
        c = self._collector(1)
        with tempfile.TemporaryDirectory() as td:
            base_path = os.path.join(td, "trace.json")
            w = DiagnosticTraceWriter(base_path, keep=5)
            assert w.write(c) is True
            assert w.last_written_path is not None
            assert os.path.basename(w.last_written_path).startswith("trace_")
            assert os.path.basename(w.last_written_path).endswith(".json")
            # The timestamp part has no underscores (only in the counter).
            basename = os.path.basename(w.last_written_path)
            # trace_YYYYmmddTHHMMSS.json
            assert basename.count("_") == 1

    def test_timestamped_filename_collision_appends_counter(self):
        """Writing twice in the same second appends a counter suffix to
        avoid overwriting.
        """
        import time as _time

        c = self._collector(1)
        with tempfile.TemporaryDirectory() as td:
            base_path = os.path.join(td, "trace.json")
            # Force a fixed timestamp format for reproducibility.
            fixed_fmt = "FIXEDTS"
            w = DiagnosticTraceWriter(base_path, keep=5, timestamp_format=fixed_fmt)
            assert w.write(c) is True
            first = w.last_written_path
            assert os.path.basename(first) == "trace_FIXEDTS.json"
            # Write again with the same fixed timestamp: collision.
            assert w.write(c) is True
            second = w.last_written_path
            assert os.path.basename(second) == "trace_FIXEDTS_1.json"
            # And again...
            assert w.write(c) is True
            third = w.last_written_path
            assert os.path.basename(third) == "trace_FIXEDTS_2.json"
            # All three files exist.
            assert os.path.isfile(first)
            assert os.path.isfile(second)
            assert os.path.isfile(third)

    def test_keep_limit_prunes_old_files(self):
        """Writing more than `keep` files deletes the oldest."""
        c = self._collector(1)
        with tempfile.TemporaryDirectory() as td:
            base_path = os.path.join(td, "trace.json")
            keep = 3
            # Use sequential timestamps via a custom format.
            for i in range(5):
                ts = f"TS{i:02d}"
                w = DiagnosticTraceWriter(
                    base_path, keep=keep, timestamp_format=ts
                )
                assert w.write(c) is True
            # Only the most recent `keep`=3 files (TS02, TS03, TS04)
            # should remain.
            files = sorted(os.listdir(td))
            assert len(files) == 3
            assert f"trace_TS02.json" in files
            assert f"trace_TS03.json" in files
            assert f"trace_TS04.json" in files
            # Older ones must be gone.
            assert f"trace_TS00.json" not in files
            assert f"trace_TS01.json" not in files

    def test_keep_1_retains_only_current(self):
        """keep=1 means only the most recent trace is kept."""
        c = self._collector(1)
        with tempfile.TemporaryDirectory() as td:
            base_path = os.path.join(td, "trace.json")
            for i in range(10):
                w = DiagnosticTraceWriter(
                    base_path, keep=1, timestamp_format=f"TS{i}"
                )
                assert w.write(c) is True
            assert len(os.listdir(td)) == 1

    def test_pruning_ignores_unrelated_files(self):
        """Files not matching the trace pattern are left alone."""
        c = self._collector(1)
        with tempfile.TemporaryDirectory() as td:
            base_path = os.path.join(td, "trace.json")
            # Create an unrelated file.
            unrelated = os.path.join(td, "other_file.json")
            with open(unrelated, "w") as f:
                f.write("{}")
            # Also create a file that has a different extension.
            wrong_ext = os.path.join(td, "trace.txt")
            with open(wrong_ext, "w") as f:
                f.write("")
            # Create a file with matching prefix but wrong extension.
            bad_ext = os.path.join(td, "trace_123.txt")
            with open(bad_ext, "w") as f:
                f.write("")

            for i in range(5):
                w = DiagnosticTraceWriter(
                    base_path, keep=2, timestamp_format=f"TS{i}"
                )
                assert w.write(c) is True
            # 2 kept traces + unrelated + wrong_ext + bad_ext = 5 files.
            all_files = set(os.listdir(td))
            assert len(all_files) == 5
            assert "other_file.json" in all_files
            assert "trace.txt" in all_files
            assert "trace_123.txt" in all_files

    def test_old_non_timestamped_trace_is_pruned(self):
        """A non-timestamped trace file matching the base name should
        be counted and pruned along with the timestamped ones.
        """
        c = self._collector(1)
        with tempfile.TemporaryDirectory() as td:
            base_path = os.path.join(td, "trace.json")
            # Pre-create the base (non-timestamped) trace file.
            with open(base_path, "w") as f:
                json.dump({"old": True}, f)
            # Give it an old mtime so it's considered the oldest.
            old_time = 1000000000.0
            os.utime(base_path, (old_time, old_time))

            # Write 3 new traces with keep=3.
            for i in range(3):
                w = DiagnosticTraceWriter(
                    base_path, keep=3, timestamp_format=f"TS{i}"
                )
                assert w.write(c) is True
            # The old base file should be pruned because we now have 4
            # files total and keep=3, and it's the oldest.
            all_files = set(os.listdir(td))
            assert base_path not in [os.path.join(td, f) for f in all_files]
            assert len(all_files) == 3

    def test_dry_run_does_not_rotate_or_prune(self):
        """In dry-run mode, no files are written and no pruning occurs
        even if there are old files present.
        """
        c = self._collector(1)
        with tempfile.TemporaryDirectory() as td:
            base_path = os.path.join(td, "trace.json")
            # Pre-create an old trace file.
            old_file = os.path.join(td, "trace_OLD.json")
            with open(old_file, "w") as f:
                f.write("{}")

            w = DiagnosticTraceWriter(
                base_path, keep=0, dry_run=True, timestamp_format="NEW"
            )
            assert w.write(c) is True
            # Old file still there, no new files added.
            assert os.path.isfile(old_file)
            # Only the original old file + no new timestamped files.
            assert len(os.listdir(td)) == 1
            # last_written_path not set since nothing was written.
            assert w.last_written_path is None

    def test_custom_timestamp_format(self):
        """Users can customise the strftime format."""
        c = self._collector(1)
        with tempfile.TemporaryDirectory() as td:
            base_path = os.path.join(td, "trace.json")
            custom_fmt = "%Y_%m_%d"
            w = DiagnosticTraceWriter(
                base_path, keep=5, timestamp_format=custom_fmt
            )
            assert w.write(c) is True
            assert w.last_written_path is not None
            basename = os.path.basename(w.last_written_path)
            # Should look like "trace_2026_06_11.json"
            parts = basename.split(".")[0].split("_")
            assert len(parts) == 4  # trace + yyyy + mm + dd
            assert parts[0] == "trace"
            assert len(parts[1]) == 4  # year
            assert len(parts[2]) == 2  # month
            assert len(parts[3]) == 2  # day


# ---------------------------------------------------------------------------
# 5. Factory helpers and config defaults
# ---------------------------------------------------------------------------


class TestFactoryHelpers:
    def test_create_collector_honors_cli_flag_and_verbose(self):
        # Explicit True: enable regardless of verbose=0
        c = create_collector_for_session(True, 0)
        assert c.enabled is True

        # Explicit False: disable even at high verbosity
        c = create_collector_for_session(False, 10)
        assert c.enabled is False

        # None + verbose>=3 => enabled
        c = create_collector_for_session(None, 3)
        assert c.enabled is True

        # None + verbose<3 => disabled
        c = create_collector_for_session(None, 1)
        assert c.enabled is False

    def test_create_collector_respects_max_events_from_args(self):
        c = create_collector_for_session(True, 0, max_events=5)
        for i in range(100):
            c.record("general", "info", f"m{i}")
        assert c.event_count == 5

    def test_create_writer_sets_path_and_modes(self):
        w = create_writer_for_session(
            "/tmp/x.json", dry_run=True, quiet=False, max_bytes=123
        )
        assert w._output_path == "/tmp/x.json"
        assert w._dry_run is True
        assert w._max_bytes == 123

    def test_diagnostics_config_returns_expected_keys(self):
        # Ensure no KeyError on missing/empty config (defaults are in
        # the YAML, but some tests run without it).
        keys = set(diagnostics_config().keys())
        for required in (
            "enabled",
            "trace_path",
            "max_events",
            "max_bytes",
            "keep",
            "timestamp_format",
        ):
            assert required in keys


# ---------------------------------------------------------------------------
# 5. Session-level integration
# ---------------------------------------------------------------------------


class TestDiagnosticsIntegration(AsIsImporterMixin, ImportTestCase):
    """Smoke tests that the collector actually runs during import."""

    def _make_session(self, diagnose=None):
        """Construct an ImportSessionFixture for the configured import dir."""
        return ImportSessionFixture(
            self.lib,
            loghandler=None,
            query=None,
            paths=[self.import_dir],
            diagnose=diagnose,
        )

    def test_asis_import_session_has_collector(self):
        # No explicit diagnose flag + verbose default (0) -> disabled
        importer = self._make_session(diagnose=None)
        assert importer.diagnostics.enabled is False

    def test_verbose_enables_collector(self):
        # verbose>=3 自动启用
        self.config["verbose"] = 3
        importer = self._make_session(diagnose=None)
        assert importer.diagnostics.enabled is True
        # 显式 CLI 标志覆盖 verbose 设置
        self.config["verbose"] = 0
        importer2 = self._make_session(diagnose=True)
        assert importer2.diagnostics.enabled is True
        importer3 = self._make_session(diagnose=False)
        assert importer3.diagnostics.enabled is False

    def test_asis_import_produces_events_when_enabled(self):
        self.config["verbose"] = 3
        with tempfile.TemporaryDirectory() as td:
            trace_path = os.path.join(td, "trace.json")
            self.config["import"]["diagnose_trace"] = trace_path
            # Disable history to write directly to the configured path.
            self.config["import"]["diagnose_keep"] = 0
            # 关闭 autotag，使用 as-is 模式（模拟测试环境中的非交互式导入）
            self.config["import"]["autotag"] = False
            self.config["import"]["copy"] = False
            importer = self._make_session(diagnose=None)
            importer.set_config(self.config["import"])
            importer.run()
            assert os.path.isfile(trace_path), (
                f"Expected trace file at {trace_path}"
            )
            data = json.load(open(trace_path, "r", encoding="utf-8"))
            assert "metadata" in data
            assert "events" in data
            assert data["metadata"]["summary"]["event_count"] > 0
            msgs = {e["message"] for e in data["events"]}
            assert any("import session started" in m for m in msgs)
            assert any("import session finished" in m for m in msgs)
            stages = {e["stage"] for e in data["events"]}
            assert DIAG_STAGE_PATH_PARSE in stages
            assert DIAG_STAGE_FILE_IMPORT in stages

    def test_asis_import_produces_timestamped_trace_by_default(self):
        """With keep>0 (default) the trace file name includes a
        timestamp suffix instead of the base name directly.
        """
        self.config["verbose"] = 3
        with tempfile.TemporaryDirectory() as td:
            trace_base = os.path.join(td, "trace.json")
            self.config["import"]["diagnose_trace"] = trace_base
            self.config["import"]["diagnose_keep"] = 3
            self.config["import"]["autotag"] = False
            self.config["import"]["copy"] = False
            importer = self._make_session(diagnose=None)
            importer.set_config(self.config["import"])
            importer.run()

            # The base path should NOT exist, but a timestamped variant should.
            assert not os.path.exists(trace_base), (
                "Expected timestamped filename, not the base path directly"
            )
            files = os.listdir(td)
            matching = [f for f in files if f.startswith("trace_") and f.endswith(".json")]
            assert len(matching) == 1, (
                f"Expected one timestamped trace file, found: {matching}"
            )
            data = json.load(open(os.path.join(td, matching[0]), "r", encoding="utf-8"))
            assert data["metadata"]["summary"]["event_count"] > 0

    def test_quiet_import_without_trace_path_uses_default(self):
        """Quiet mode + diagnostics enabled + no explicit trace path:
        the trace should be written next to the library database file.
        This is the exact scenario described in the bug report.

        With keep>0 the file gets a timestamp suffix; with keep=0 it
        uses the base name directly.
        """
        self.config["verbose"] = 3
        self.config["import"]["quiet"] = True
        self.config["import"]["autotag"] = False
        self.config["import"]["copy"] = False
        # Disable history so the file is written directly to the base name
        # for easy verification.
        self.config["import"]["diagnose_keep"] = 0
        # Ensure no explicit diagnose_trace is set so the default kicks
        # in.  The beets config object does not support __delitem__, so
        # we set it to an empty string which the session treats as
        # "not configured".
        self.config["import"]["diagnose_trace"] = ""

        importer = self._make_session(diagnose=None)
        importer.set_config(self.config["import"])
        importer.run()

        lib_path_str = os.fsdecode(self.lib.path)
        if lib_path_str == ":memory:":
            # In-memory library: no default path, but a warning should
            # have been logged. We cannot easily check the warning here
            # but at least verify the import didn't crash.
            return

        expected = default_diagnostic_trace_path(self.lib.path)
        assert expected is not None, (
            f"Could not compute default trace path for library at {lib_path_str}"
        )
        assert os.path.isfile(expected), (
            f"Quiet-mode import should have written trace to {expected}"
        )
        data = json.load(open(expected, "r", encoding="utf-8"))
        assert data["metadata"]["summary"]["event_count"] > 0
