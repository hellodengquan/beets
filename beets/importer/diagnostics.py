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

"""Import failure diagnostics: verbose information collection and
structured trace output for debugging metadata matching, path parsing,
and file import errors.

Provides:
- ``ImportDiagnosticCollector``: accumulates diagnostic events during import
- ``DiagnosticTraceWriter``: writes structured JSON trace with size limits
- Integration points: called from pipeline stages, tasks, and session
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import TYPE_CHECKING, Any

from beets import config, logging, util
from beets.util import displayable_path, syspath

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from beets import library

    from .tasks import ImportTask


log = logging.getLogger("beets")

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

DIAG_STAGE_METADATA_MATCH = "metadata_match"
DIAG_STAGE_PATH_PARSE = "path_parse"
DIAG_STAGE_FILE_IMPORT = "file_import"
DIAG_STAGE_USER_CHOICE = "user_choice"
DIAG_STAGE_DUPLICATE = "duplicate_resolve"
DIAG_STAGE_GENERAL = "general"

DIAG_LEVEL_INFO = "info"
DIAG_LEVEL_WARNING = "warning"
DIAG_LEVEL_ERROR = "error"
DIAG_LEVEL_DEBUG = "debug"


@dataclass
class DiagnosticEvent:
    """A single diagnostic record emitted during the import."""

    timestamp: float
    stage: str
    level: str
    message: str
    task_paths: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    exception: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


class ImportDiagnosticCollector:
    """Collects diagnostic events for an import session.

    The collector is *thread-safe only in the sense that individual event
    appends do not corrupt internal state; it is not fully synchronised
    because the import pipeline uses a single-threaded sequential mode
    when threaded=False (default) and parallel workers only share the
    collector through reference assignment which is atomic on CPython.
    """

    def __init__(
        self,
        enabled: bool = False,
        max_events: int = 50000,
        verbose_threshold: int = 2,
    ) -> None:
        self.enabled = enabled
        self.verbose_threshold = verbose_threshold
        self._events: deque[DiagnosticEvent] = deque(maxlen=max_events)
        self._error_count = 0
        self._warning_count = 0
        self._start_time = time.time()

    # -- lifecycle ---------------------------------------------------------

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False

    # -- recording ---------------------------------------------------------

    def record(
        self,
        stage: str,
        level: str,
        message: str,
        *,
        task: ImportTask | None = None,
        paths: Sequence[bytes] | None = None,
        context: dict[str, Any] | None = None,
        exc_info: BaseException | None = None,
    ) -> None:
        """Record a diagnostic event if collection is enabled.

        ``exc_info`` may be an exception instance; its traceback and
        type are serialised into the event.
        """
        if not self.enabled:
            return

        task_paths: list[str] = []
        if task is not None and getattr(task, "paths", None):
            task_paths = [displayable_path(p) for p in task.paths]
        elif paths is not None:
            task_paths = [displayable_path(p) for p in paths]

        exc_dict: dict[str, Any] | None = None
        if exc_info is not None:
            exc_dict = self._format_exception(exc_info)

        self._events.append(
            DiagnosticEvent(
                timestamp=time.time(),
                stage=stage,
                level=level,
                message=message,
                task_paths=task_paths,
                context=context or {},
                exception=exc_dict,
            )
        )

        if level == DIAG_LEVEL_ERROR:
            self._error_count += 1
        elif level == DIAG_LEVEL_WARNING:
            self._warning_count += 1

    # -- metadata match helpers -------------------------------------------

    def record_metadata_lookup(
        self,
        task: ImportTask,
        cur_artist: str | None,
        cur_album: str | None,
        num_candidates: int,
        recommendation: str,
        *,
        search_ids: list[str] | None = None,
    ) -> None:
        """Record the result of a metadata candidate lookup."""
        self.record(
            DIAG_STAGE_METADATA_MATCH,
            DIAG_LEVEL_INFO,
            "metadata lookup completed",
            task=task,
            context={
                "cur_artist": cur_artist,
                "cur_album": cur_album,
                "num_candidates": num_candidates,
                "recommendation": recommendation,
                "search_ids": search_ids or [],
            },
        )

    def record_metadata_candidate(
        self,
        task: ImportTask,
        index: int,
        artist: str,
        album: str,
        distance: float,
        distance_string: str,
        penalty_keys: list[str],
        disambiguation: str | None = None,
    ) -> None:
        """Record details about an individual metadata candidate."""
        self.record(
            DIAG_STAGE_METADATA_MATCH,
            DIAG_LEVEL_DEBUG,
            f"candidate #{index + 1}: {artist} - {album}",
            task=task,
            context={
                "index": index,
                "artist": artist,
                "album": album,
                "distance": distance,
                "distance_string": distance_string,
                "penalty_keys": penalty_keys,
                "disambiguation": disambiguation or "",
            },
        )

    def record_metadata_choice(
        self,
        task: ImportTask,
        choice: str,
        match_artist: str | None = None,
        match_album: str | None = None,
    ) -> None:
        """Record the final metadata match decision for a task."""
        self.record(
            DIAG_STAGE_METADATA_MATCH,
            DIAG_LEVEL_INFO,
            f"match choice: {choice}",
            task=task,
            context={
                "choice": choice,
                "match_artist": match_artist,
                "match_album": match_album,
            },
        )

    # -- path parse helpers -----------------------------------------------

    def record_path_walk(
        self,
        toppath: bytes,
        root: bytes,
        num_files: int,
        *,
        ignored_files: list[str] | None = None,
    ) -> None:
        """Record a directory walk event."""
        self.record(
            DIAG_STAGE_PATH_PARSE,
            DIAG_LEVEL_DEBUG,
            f"walked directory: {displayable_path(root)}",
            paths=[toppath, root],
            context={
                "toppath": displayable_path(toppath),
                "root": displayable_path(root),
                "num_files": num_files,
                "ignored_files": ignored_files or [],
            },
        )

    def record_item_read(
        self,
        path: bytes,
        success: bool,
        *,
        exc_info: BaseException | None = None,
        reason: str = "",
    ) -> None:
        """Record an attempt to read a media file as an Item."""
        level = DIAG_LEVEL_INFO if success else DIAG_LEVEL_ERROR
        self.record(
            DIAG_STAGE_PATH_PARSE,
            level,
            "read item: {} {}".format(
                displayable_path(path), "ok" if success else f"failed: {reason}"
            ),
            paths=[path],
            context={
                "path": displayable_path(path),
                "success": success,
                "reason": reason,
            },
            exc_info=exc_info if not success else None,
        )

    def record_archive_extract(
        self,
        archive_path: bytes,
        target_dir: bytes,
        success: bool,
        *,
        exc_info: BaseException | None = None,
    ) -> None:
        self.record(
            DIAG_STAGE_PATH_PARSE,
            DIAG_LEVEL_INFO if success else DIAG_LEVEL_ERROR,
            "archive extract: {} -> {} {}".format(
                displayable_path(archive_path),
                displayable_path(target_dir),
                "ok" if success else "failed",
            ),
            paths=[archive_path, target_dir],
            context={
                "archive": displayable_path(archive_path),
                "target": displayable_path(target_dir),
                "success": success,
            },
            exc_info=exc_info,
        )

    # -- file import helpers ----------------------------------------------

    def record_file_operation(
        self,
        task: ImportTask,
        operation: str,
        item_path: bytes,
        new_path: bytes | None,
        success: bool,
        *,
        exc_info: BaseException | None = None,
    ) -> None:
        self.record(
            DIAG_STAGE_FILE_IMPORT,
            DIAG_LEVEL_INFO if success else DIAG_LEVEL_ERROR,
            "file {}: {} -> {} {}".format(
                operation,
                displayable_path(item_path),
                displayable_path(new_path) if new_path else "n/a",
                "ok" if success else "failed",
            ),
            task=task,
            context={
                "operation": operation,
                "item_path": displayable_path(item_path),
                "new_path": displayable_path(new_path) if new_path else None,
                "success": success,
            },
            exc_info=exc_info,
        )

    def record_library_add(
        self,
        task: ImportTask,
        num_items: int,
        success: bool,
        *,
        exc_info: BaseException | None = None,
    ) -> None:
        self.record(
            DIAG_STAGE_FILE_IMPORT,
            DIAG_LEVEL_INFO if success else DIAG_LEVEL_ERROR,
            "library add: {} items {}".format(
                num_items, "ok" if success else "failed"
            ),
            task=task,
            context={"num_items": num_items, "success": success},
            exc_info=exc_info,
        )

    def record_metadata_write(
        self,
        item: library.Item,
        success: bool,
        *,
        task: ImportTask | None = None,
        exc_info: BaseException | None = None,
    ) -> None:
        self.record(
            DIAG_STAGE_FILE_IMPORT,
            DIAG_LEVEL_INFO if success else DIAG_LEVEL_ERROR,
            "metadata write: {} {}".format(
                displayable_path(item.path), "ok" if success else "failed"
            ),
            task=task,
            paths=[item.path],
            context={
                "path": displayable_path(item.path),
                "success": success,
            },
            exc_info=exc_info,
        )

    # -- duplicate helpers ------------------------------------------------

    def record_duplicate_found(
        self,
        task: ImportTask,
        num_duplicates: int,
        duplicate_ids: list[int],
        action: str,
    ) -> None:
        self.record(
            DIAG_STAGE_DUPLICATE,
            DIAG_LEVEL_WARNING,
            f"found {num_duplicates} duplicates, action: {action}",
            task=task,
            context={
                "num_duplicates": num_duplicates,
                "duplicate_ids": duplicate_ids,
                "action": action,
            },
        )

    # -- introspection -----------------------------------------------------

    @property
    def event_count(self) -> int:
        return len(self._events)

    @property
    def error_count(self) -> int:
        return self._error_count

    @property
    def warning_count(self) -> int:
        return self._warning_count

    def events(self) -> list[DiagnosticEvent]:
        return list(self._events)

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "event_count": self.event_count,
            "error_count": self._error_count,
            "warning_count": self._warning_count,
            "duration_seconds": round(time.time() - self._start_time, 3),
        }

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _format_exception(exc: BaseException) -> dict[str, Any]:
        try:
            tb_str = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
        except Exception:
            tb_str = traceback.format_exc()
        return {
            "type": type(exc).__name__,
            "module": type(exc).__module__,
            "message": str(exc),
            "repr": repr(exc),
            "traceback": tb_str,
        }


# ---------------------------------------------------------------------------
# Trace writer
# ---------------------------------------------------------------------------


class DiagnosticTraceWriter:
    """Writes collected diagnostic events to a structured JSON trace file.

    Features:
    - size limit (in bytes) with automatic truncation of oldest events
    - event count limit
    - graceful fallback on write errors (stdout summary + warning log)
    - ``dry_run`` mode that serialises but never touches the filesystem
    - history retention: the latest trace is always written to the
      configured fixed path; when ``keep > 0``, a timestamped copy is
      also saved alongside and old copies beyond ``keep`` are pruned.
      This means downstream scripts can always read the latest data
      from the same path, while history is available for comparison.
    """

    DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB
    DEFAULT_MAX_EVENTS = 20000
    DEFAULT_KEEP = 5
    DEFAULT_TIMESTAMP_FMT = "%Y%m%dT%H%M%S"
    TRACE_VERSION = 1

    def __init__(
        self,
        output_path: str | bytes | os.PathLike | None,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_events: int = DEFAULT_MAX_EVENTS,
        pretty: bool = True,
        dry_run: bool = False,
        quiet: bool = False,
        keep: int = DEFAULT_KEEP,
        timestamp_format: str = DEFAULT_TIMESTAMP_FMT,
    ) -> None:
        self._output_path = (
            os.fsdecode(output_path) if output_path is not None else None
        )
        self._max_bytes = max_bytes
        self._max_events = max_events
        self._pretty = pretty
        self._dry_run = dry_run
        self._quiet = quiet
        self._keep = keep
        self._timestamp_format = timestamp_format
        self._write_error: str | None = None
        self._last_written_path: str | None = None

    # -- public API --------------------------------------------------------

    @property
    def output_path(self) -> str | None:
        return self._output_path

    @property
    def last_written_path(self) -> str | None:
        """The path the trace was most recently written to.

        This is always ``output_path`` — the latest trace is always
        written to the configured fixed path so that downstream scripts
        can rely on it.  Timestamped history copies are stored alongside
        but are not reflected here.
        """
        return self._last_written_path

    @property
    def write_error(self) -> str | None:
        return self._write_error

    def write(
        self,
        collector: ImportDiagnosticCollector,
        *,
        extra_metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Serialise the collector's events to the output path.

        Returns True on success, False if a write error occurred (which
        is also recorded in ``self.write_error`` and logged as a
        warning). In dry-run mode the method returns True and logs the
        would-be path at info level.
        """
        events = collector.events()
        summary = collector.summary()

        if self._max_events and len(events) > self._max_events:
            dropped = len(events) - self._max_events
            log.warning(
                "Diagnostic trace truncated: dropping {} oldest events "
                "(limit {})",
                dropped,
                self._max_events,
            )
            events = events[-self._max_events :]
            summary["events_truncated"] = dropped

        metadata: dict[str, Any] = {
            "trace_version": self.TRACE_VERSION,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "summary": summary,
            "beets_version": util.__version__
            if hasattr(util, "__version__")
            else "unknown",
            "python_version": sys.version,
            "platform": sys.platform,
        }
        if extra_metadata:
            metadata.update(extra_metadata)

        payload = {
            "metadata": metadata,
            "events": [e.to_dict() for e in events],
        }

        serialised = self._serialise(payload)

        # Enforce byte-size limit by trimming oldest events.
        serialised = self._trim_to_size(serialised, payload, events, summary)

        if self._dry_run:
            log.info(
                "Diagnostic trace (dry-run): {} bytes, {} events "
                "would be written to {}",
                len(serialised),
                len(events),
                self._output_path or "<stdout>",
            )
            return True

        if self._output_path is None:
            if self._quiet:
                # Quiet mode with no trace path.  This should not
                # normally happen because session-level code resolves
                # a default path, but if we end up here it means
                # diagnostics were collected with nowhere to persist
                # them.  Warn loudly so the user knows data was lost.
                if collector.event_count > 0:
                    log.warning(
                        "Diagnostics were enabled but no trace output "
                        "path could be determined; {} events and {} "
                        "errors were collected but discarded. Set "
                        "import.diagnose_trace or use --diagnose-trace "
                        "to specify an output file.",
                        collector.event_count,
                        collector.error_count,
                    )
                return True
            self._print_to_stdout(serialised)
            return True

        return self._write_to_file(serialised)

    # -- internals ---------------------------------------------------------

    def _serialise(self, payload: dict[str, Any]) -> str:
        kwargs: dict[str, Any] = {"default": str, "ensure_ascii": False}
        if self._pretty:
            kwargs["indent"] = 2
            kwargs["sort_keys"] = False
        try:
            return json.dumps(payload, **kwargs)
        except (TypeError, ValueError) as exc:
            log.warning(
                "Diagnostic trace: pretty serialisation failed ({}), "
                "retrying without formatting.",
                exc,
            )
            kwargs.pop("indent", None)
            kwargs.pop("sort_keys", None)
            return json.dumps(payload, **kwargs)

    def _trim_to_size(
        self,
        current: str,
        payload: dict[str, Any],
        events: list[DiagnosticEvent],
        summary: dict[str, Any],
    ) -> str:
        if self._max_bytes <= 0 or len(current.encode("utf-8")) <= self._max_bytes:
            return current

        # Drop events in batches until under the limit. Preserve metadata.
        batch = max(1, len(events) // 10)
        trimmed_events = events[:]
        while trimmed_events and len(current.encode("utf-8")) > self._max_bytes:
            trimmed_events = trimmed_events[batch:]
            payload["events"] = [e.to_dict() for e in trimmed_events]
            payload["metadata"]["summary"]["events_size_truncated"] = len(
                events
            ) - len(trimmed_events)
            current = self._serialise(payload)

        if not trimmed_events:
            # Even with zero events the metadata itself is oversize.
            # Just return the metadata-only payload.
            payload["events"] = []
            payload["metadata"]["summary"]["events_dropped_entirely"] = True
            current = self._serialise(payload)

        log.warning(
            "Diagnostic trace size exceeded {} bytes; kept {} events "
            "({} bytes).",
            self._max_bytes,
            len(trimmed_events),
            len(current.encode("utf-8")),
        )
        return current

    def _write_to_file(self, content: str) -> bool:
        assert self._output_path is not None
        try:
            output_dir = os.path.dirname(self._output_path)
            if output_dir and not os.path.isdir(syspath(output_dir)):
                os.makedirs(syspath(output_dir), exist_ok=True)

            with open(syspath(self._output_path), "w", encoding="utf-8") as fp:
                fp.write(content)

            self._last_written_path = self._output_path
            log.info(
                "Diagnostic trace written to {} ({} bytes, {} events)",
                self._output_path,
                len(content.encode("utf-8")),
                content.count('"stage":'),
            )

            if self._keep > 0:
                hist_path = self._generate_timestamped_path()
                try:
                    with open(syspath(hist_path), "w", encoding="utf-8") as fp:
                        fp.write(content)
                    log.debug(
                        "Diagnostic trace history copy: {}", hist_path
                    )
                except OSError as exc:
                    log.debug(
                        "Could not write history copy {}: {}", hist_path, exc
                    )
                self._prune_old_traces()

            return True
        except OSError as exc:
            self._write_error = str(exc)
            log.warning(
                "Failed to write diagnostic trace to {}: {}. "
                "Falling back to terminal summary.",
                self._output_path,
                exc,
            )
            self._print_summary_stderr()
            return False
        except Exception as exc:  # noqa: BLE001 - never let diagnostics crash import
            self._write_error = f"{type(exc).__name__}: {exc}"
            log.warning(
                "Unexpected error writing diagnostic trace: {}", exc
            )
            self._print_summary_stderr()
            return False

    def _generate_timestamped_path(self) -> str:
        """Generate a timestamped variant of ``self._output_path``.

        For example, if the configured path is ``/tmp/trace.json`` and
        the current time is 2026-06-11 22:30:45, the result is
        ``/tmp/trace_20260611T223045.json``. In the rare case where the
        timestamp already exists (two writes in the same second), a
        counter suffix is appended to avoid overwriting.
        """
        assert self._output_path is not None
        base, ext = os.path.splitext(self._output_path)
        ts = time.strftime(self._timestamp_format or self.DEFAULT_TIMESTAMP_FMT)
        candidate = f"{base}_{ts}{ext}"
        counter = 1
        while os.path.exists(syspath(candidate)):
            candidate = f"{base}_{ts}_{counter}{ext}"
            counter += 1
        return candidate

    def _prune_old_traces(self) -> None:
        """Remove timestamped history files beyond the keep limit.

        Only files matching ``<stem>_*<ext>`` (the timestamped copies)
        are considered for pruning.  The primary output file
        (``self._output_path``) is never pruned — it always holds the
        latest trace so that downstream scripts can rely on a fixed path.
        """
        if self._keep <= 0 or self._output_path is None:
            return

        try:
            base_dir = os.path.dirname(self._output_path) or "."
            base_name = os.path.basename(self._output_path)
            stem, ext = os.path.splitext(base_name)

            pattern_prefix = stem + "_"

            candidates: list[tuple[float, str]] = []
            for entry in os.listdir(syspath(base_dir)):
                entry_path = os.path.join(base_dir, entry)
                if not os.path.isfile(syspath(entry_path)):
                    continue
                if entry == base_name:
                    continue
                if entry.startswith(pattern_prefix) and entry.endswith(ext):
                    try:
                        mtime = os.path.getmtime(syspath(entry_path))
                        candidates.append((mtime, entry_path))
                    except OSError:
                        continue

            candidates.sort(key=lambda x: x[0], reverse=True)

            kept = 0
            for mtime, path in candidates:
                if kept < self._keep:
                    kept += 1
                else:
                    try:
                        os.remove(syspath(path))
                        log.debug(
                            "Pruned old diagnostic trace (keep={}): {}",
                            self._keep,
                            path,
                        )
                    except OSError as exc:
                        log.debug(
                            "Could not prune old trace {}: {}",
                            path,
                            exc,
                        )
        except OSError as exc:
            log.debug(
                "Error while pruning old diagnostic traces: {}", exc
            )

    def _print_to_stdout(self, content: str) -> None:
        # Write via our own print_ helper when possible to respect
        # terminal encoding. Avoid importing top-level to prevent cycles.
        try:
            from beets import ui

            ui.print_(content)
        except Exception:  # noqa: BLE001
            sys.stdout.write(content)
            sys.stdout.write("\n")

    def _print_summary_stderr(self) -> None:
        try:
            from beets import ui

            ui.print_("--- Diagnostic summary (trace write failed) ---")
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Helpers: session integration glue
# ---------------------------------------------------------------------------


def diagnostics_config() -> dict[str, Any]:
    """Read diagnostic-related settings from the global config."""
    icfg = config["import"]
    return {
        "enabled": icfg["diagnose"].get(bool),
        "trace_path": icfg["diagnose_trace"].get() or None,
        "max_events": icfg["diagnose_max_events"].get(int),
        "max_bytes": icfg["diagnose_max_bytes"].get(int),
        "keep": icfg["diagnose_keep"].get(int),
        "timestamp_format": icfg["diagnose_timestamp_format"].get(str),
    }


def should_enable_diagnostics(
    cli_diagnose: bool | None, verbose_level: int
) -> bool:
    """Decide whether to enable diagnostics based on CLI + verbosity.

    - Explicit ``--diagnose`` flag: always enable.
    - Explicit ``--no-diagnose``: always disable.
    - Otherwise: enable if verbose >= 3 (extra_debug territory) so very
      loud users automatically get traces without extra typing.
    """
    if cli_diagnose is True:
        return True
    if cli_diagnose is False:
        return False
    return verbose_level >= 3


def create_collector_for_session(
    cli_diagnose: bool | None,
    verbose_level: int,
    *,
    max_events: int | None = None,
) -> ImportDiagnosticCollector:
    enabled = should_enable_diagnostics(cli_diagnose, verbose_level)
    max_events = max_events or (
        config["import"]["diagnose_max_events"].get(int)
        if config["import"]["diagnose_max_events"].exists()
        else 50000
    )
    collector = ImportDiagnosticCollector(
        enabled=enabled, max_events=max_events, verbose_threshold=2
    )
    return collector


def default_diagnostic_trace_path(library_path: str | bytes | None) -> str | None:
    """Compute a sensible default trace output path.

    The default is placed next to the beets library database file with
    a ``.diagnostics.json`` suffix.  For example, if the library is at
    ``~/beetslibrary.db`` the default trace path is
    ``~/beetslibrary.diagnostics.json``.

    Returns None if the library path cannot be determined.
    """
    if library_path is None:
        return None
    lib_str = os.fsdecode(library_path) if isinstance(library_path, bytes) else library_path
    lib_str = lib_str.strip()
    if not lib_str:
        return None
    # For in-memory databases (e.g. ":memory:") there is no directory
    # to write alongside.
    if lib_str == ":memory:":
        return None
    base, _ = os.path.splitext(lib_str)
    if not base:
        return None
    return f"{base}.diagnostics.json"


def create_writer_for_session(
    trace_path: str | None,
    *,
    dry_run: bool = False,
    quiet: bool = False,
    max_bytes: int | None = None,
    max_events: int | None = None,
    keep: int | None = None,
    timestamp_format: str | None = None,
    library_path: str | bytes | None = None,
) -> DiagnosticTraceWriter:
    """Build a trace writer respecting quiet/dry-run modes.

    Resolution order for the output path:

    1. Explicit ``trace_path`` from ``--diagnose-trace`` or config → use as-is.
    2. No explicit path, quiet mode → fall back to the default path
       derived from the library database location so the trace is
       always persisted somewhere.
    3. No explicit path, non-quiet → stdout (as before).

    If the default path cannot be determined (e.g. in-memory library)
    and no explicit path is given, the writer will emit a warning when
    it has events to write but nowhere to put them.
    """
    resolved_path: str | None
    if trace_path:
        resolved_path = trace_path
    elif quiet:
        # Quiet mode with no explicit path: compute a default file
        # path so diagnostics are not silently discarded.
        resolved_path = default_diagnostic_trace_path(library_path)
        if resolved_path is None:
            # We truly have nowhere to write.  The writer will emit
            # a warning at write time.
            resolved_path = None
    else:
        resolved_path = None

    # Resolve keep count and timestamp format, falling back to config
    # values, then to class defaults.
    if keep is None:
        keep = (
            config["import"]["diagnose_keep"].get(int)
            if config["import"]["diagnose_keep"].exists()
            else DiagnosticTraceWriter.DEFAULT_KEEP
        )
    if timestamp_format is None:
        timestamp_format = (
            config["import"]["diagnose_timestamp_format"].get(str)
            if config["import"]["diagnose_timestamp_format"].exists()
            else DiagnosticTraceWriter.DEFAULT_TIMESTAMP_FMT
        )

    return DiagnosticTraceWriter(
        resolved_path,
        max_bytes=max_bytes
        or (
            config["import"]["diagnose_max_bytes"].get(int)
            if config["import"]["diagnose_max_bytes"].exists()
            else DiagnosticTraceWriter.DEFAULT_MAX_BYTES
        ),
        max_events=max_events
        or (
            config["import"]["diagnose_max_events"].get(int)
            if config["import"]["diagnose_max_events"].exists()
            else DiagnosticTraceWriter.DEFAULT_MAX_EVENTS
        ),
        dry_run=dry_run,
        quiet=quiet,
        keep=keep,
        timestamp_format=timestamp_format,
    )
