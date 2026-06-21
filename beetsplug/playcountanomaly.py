# This file is part of beets.
# Copyright 2016
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

"""Detect and review anomalous play counts.

This plugin identifies songs with suspicious play count patterns, such as:
- Play counts that deviate significantly from the personal average
- Newly added songs with an unusually high play frequency
- Physically impossible play counts (suggesting concurrent/duplicate counting)
- Songs with recent plays but zero or very low play counts (suggesting resets)
- Single-day play count spikes far above the personal average
- Concurrent plays across multiple devices suggesting duplicate counting
- Disproportionate play counts from specific devices

Users can review anomalies and choose to correct, ignore, or mark them as fake.
A detailed play history is maintained to enable accurate anomaly detection.

Data Sources & Device Identification
-------------------------------------
The plugin collects play history from multiple sources:

1. **Automatic tracking**: Monitors database changes to play_count fields and
   automatically records each play event with device context.

2. **Device identification** (4-tier fallback):
   - Explicit ``playcountanomaly.device_id`` / ``device_name`` config
   - ``BEETS_DEVICE_ID`` / ``BEETS_DEVICE_NAME`` environment variables
   - Auto-generated ``username@hostname`` from system introspection
   - Ultimate fallback: ``unknown-<pid>`` / ``Unknown Device`` when all
     system calls fail (e.g. containerized environments with no hostname)

   **Fallback activation scenarios**:
   - Docker containers with no UTS namespace (``socket.gethostname`` raises
     ``OSError`` or returns empty)
   - Minimal chroot / jail environments without user info
   - CI runners where both ``USER`` and ``LOGNAME`` env vars are unset
   - Restricted runtime environments (AppArmor, SELinux, sandboxed Python)
   - Any unhandled exception during device identification (each call is
     wrapped in try/except for safety)

3. **Programmatic API**: Other plugins can use ``record_play(item, delta, **kwargs)``
   to report plays from external sources with device and timestamp information.

4. **History table**: All play events are stored in the ``play_count_history``
   database table for anomaly analysis and audit trails.

5. **Entry point registration**: Declared in ``pyproject.toml`` under
   ``[tool.poetry.plugins."beets.plugins"]`` as a standard Python plugin
   entry point, alongside the primary beetsplug namespace package mechanism.

Verification Checklist
----------------------
The following items should be verified after any change to this plugin.
Each item includes the source code location and corresponding test reference.

V1. Plugin discovery: ``beet anomalies`` command is available after adding
    ``playcountanomaly`` to the ``plugins`` config list.
    (Mechanism: beets imports ``beetsplug.playcountanomaly`` as an implicit
    namespace package per PEP 420, finds the ``PlayCountAnomalyPlugin`` class
    via ``BeetsPlugin`` subclass check in ``plugins._get_plugin()``.
    Alternative: ``pyproject.toml:187`` entry point
    ``beets.plugins`` group.)
    *Code*: ``playcountanomaly.py:236`` -- ``PlayCountAnomalyPlugin.commands()``
    *Test*: ``test/plugins/test_playcountanomaly.py`` -- plugin loading fixtures

V2. Helper math: ``_median([]) == 0.0``, ``_median([1,3,2]) == 2.0``,
    ``_stddev([5.0], 5.0) == 0.0``.
    *Code*: ``playcountanomaly.py:130`` -- ``_median()``
    *Code*: ``playcountanomaly.py:153`` -- ``_stddev()``
    *Test*: ``test/plugins/test_playcountanomaly.py:49`` -- ``test_median()``
    *Test*: ``test/plugins/test_playcountanomaly.py:59`` -- ``test_stddev()``

V3. Z-score deviation: ``play_count=25`` with ``mean=10, stddev=5,
    threshold=2`` triggers ``zscore_deviation``; ``play_count=12`` does not.
    *Code*: ``playcountanomaly.py:508`` -- ``_check_zscore_deviation()``
    *Test*: ``test/plugins/test_playcountanomaly.py:87``
    -- ``test_zscore_deviation_high()`` / ``test_zscore_deviation_normal()``

V4. Impossible count: ``play_count=10`` with ``length=30s, added=100s ago``
    triggers ``impossible_count``; reasonable values do not.
    *Code*: ``playcountanomaly.py:584`` -- ``_check_impossible_count()``
    *Test*: ``test/plugins/test_playcountanomaly.py:123``
    -- ``test_impossible_count_true()`` / ``test_impossible_count_false()``

V5. New song burst: ``play_count=10`` added within ``new_song_days=7``
    with high ``median_per_day`` triggers ``new_song_burst``; old songs do not.
    *Code*: ``playcountanomaly.py:541`` -- ``_check_new_song_burst()``
    *Test*: ``test/plugins/test_playcountanomaly.py:113``
    -- ``test_new_song_burst()``

V6. Sudden zero: ``play_count=0`` with ``last_played < sudden_zero_days``
    triggers ``sudden_zero``; old ``last_played`` does not.
    *Code*: ``playcountanomaly.py:628`` -- ``_check_sudden_zero()``
    *Test*: ``test/plugins/test_playcountanomaly.py:139``
    -- ``test_sudden_zero()``

V7. Daily spike: history with one day at ``>= threshold * daily_mean`` plays
    triggers ``daily_spike``; uniform daily counts do not.
    *Code*: ``playcountanomaly.py:654`` -- ``_check_daily_spike()``
    *Test*: ``test/plugins/test_playcountanomaly.py:361``
    -- ``test_daily_spike_detection()`` / ``test_no_daily_spike_normal()``

V8. Concurrent play: history showing ``>= concurrent_min_plays`` from
    ``>= concurrent_device_count`` devices within ``concurrent_window_seconds``
    triggers ``concurrent_play``; single-device plays do not.
    *Code*: ``playcountanomaly.py:699`` -- ``_check_concurrent_play()``
    *Test*: ``test/plugins/test_playcountanomaly.py:401``
    -- ``test_concurrent_play_detection()``
    -- ``test_no_concurrent_play_single_device()``

V9. Device anomaly: history where one device's share ``>= threshold``
    triggers ``device_anomaly``; balanced multi-device plays do not.
    *Code*: ``playcountanomaly.py:741`` -- ``_check_device_anomaly()``
    *Test*: ``test/plugins/test_playcountanomaly.py:440``
    -- ``test_device_anomaly_detection()`` / ``test_no_device_anomaly_balanced()``

V10. Device ID fallback: ``_get_device_id()`` returns a non-empty string
     even when ``socket.gethostname()`` raises ``OSError`` or env vars
     are missing (falls back to ``unknown-<pid>``).
     ``_get_device_name()`` falls back to ``"Unknown Device"``.
     *Code*: ``playcountanomaly.py:302`` -- ``_get_device_id()``
     *Code*: ``playcountanomaly.py:335`` -- ``_get_device_name()``
     *Test*: ``test/plugins/test_playcountanomaly.py:316``
     -- ``test_device_id_fallback_no_hostname()``
     -- ``test_device_id_fallback_no_user()``
     -- ``test_device_id_env_variable()``
     -- ``test_device_id_config_overrides_env()``

V11. record_play API: ``plugin.record_play(item, delta=1, source="test",
     device_id="d1")`` returns ``True``; ``delta=0`` or ``delta=-1`` returns
     ``False``; ``enable_history_tracking=False`` returns ``False``.
     *Code*: ``playcountanomaly.py:436`` -- ``record_play()`` (public API)
     *Test*: ``test/plugins/test_playcountanomaly.py:361``
     -- ``test_record_play_api()``
"""

from __future__ import annotations

import json
import math
import os
import socket
import time
from collections import namedtuple
from typing import TYPE_CHECKING, ClassVar

from beets import config, plugins, ui
from beets.dbcore import types

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from typing import Any

    from beets.library import Item, Library

Anomaly = namedtuple("Anomaly", ["type", "score", "description"])

ANOMALY_TYPES = (
    "zscore_deviation",
    "new_song_burst",
    "impossible_count",
    "sudden_zero",
    "daily_spike",
    "concurrent_play",
    "device_anomaly",
)

RESOLUTION_IGNORED = "ignored"
RESOLUTION_CORRECTED = "corrected"
RESOLUTION_FAKE = "fake"

PLAY_HISTORY_TABLE = "play_count_history"
PLAY_HISTORY_FIELDS = {
    "id": types.PRIMARY_ID,
    "item_id": types.INTEGER,
    "play_time": types.DATE,
    "play_count": types.INTEGER,
    "play_count_delta": types.INTEGER,
    "device_id": types.STRING,
    "device_name": types.STRING,
    "source": types.STRING,
    "duration": types.FLOAT,
}



def _median(values: list[float]) -> float:
    """Calculate the median of a list of numbers."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0
    return float(sorted_vals[mid])


def _stddev(values: list[float], mean: float) -> float:
    """Calculate the population standard deviation."""
    if len(values) < 2:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


class PlayCountAnomalyPlugin(plugins.BeetsPlugin):
    """Detect and manage anomalous play counts."""

    item_types: ClassVar[dict[str, Any]] = {  # type: ignore[assignment]
        "anomaly_score": types.Float,
        "anomaly_types": types.STRING,
        "anomaly_reviewed": types.BOOLEAN,
        "anomaly_resolution": types.STRING,
        "anomaly_notes": types.STRING,
        "fake_play_count": types.INTEGER,
        "play_history": types.STRING,
        "daily_play_stats": types.STRING,
    }

    def __init__(self):
        super().__init__()
        self.config.add(
            {
                "zscore_threshold": 3.0,
                "new_song_days": 7,
                "new_song_burst_ratio": 10.0,
                "impossible_ratio_threshold": 1.5,
                "sudden_zero_days": 30,
                "sudden_zero_min_plays": 5,
                "play_count_field": "play_count",
                "last_played_field": "last_played",
                "min_plays_for_anomaly": 5,
                "daily_spike_threshold": 5.0,
                "daily_spike_min_plays": 10,
                "concurrent_window_seconds": 300,
                "concurrent_min_plays": 3,
                "concurrent_device_count": 2,
                "device_anomaly_threshold": 10.0,
                "history_retention_days": 365,
                "device_id": "",
                "device_name": "",
                "enable_history_tracking": True,
            }
        )
        config["playcountanomaly"]["play_count_field"].redact = False
        config["playcountanomaly"]["device_id"].redact = True
        self._previous_play_counts: dict[int, int] = {}
        self.register_listener("library_opened", self._on_library_opened)
        self.register_listener("database_change", self._on_database_change)

    def commands(self):
        cmd = ui.Subcommand(
            "anomalies",
            help="detect and review anomalous play counts",
            aliases=["anom"],
        )
        cmd.parser.add_option(
            "-a",
            "--all",
            dest="show_all",
            action="store_true",
            help="show all anomalies including reviewed ones",
        )
        cmd.parser.add_option(
            "-r",
            "--rescan",
            dest="rescan",
            action="store_true",
            help="re-scan all items for anomalies",
        )
        cmd.parser.add_option(
            "-s",
            "--sort",
            dest="sort_by",
            default="score",
            help="sort by 'score' or 'type' (default: score)",
        )
        cmd.parser.add_option(
            "-t",
            "--type",
            dest="filter_type",
            default="",
            help=(
                "filter by anomaly type (zscore_deviation, new_song_burst, "
                "impossible_count, sudden_zero, daily_spike, concurrent_play, "
                "device_anomaly)"
            ),
        )
        cmd.parser.add_option(
            "--ignore",
            dest="ignore",
            action="store_true",
            help="mark matching items' anomalies as ignored",
        )
        cmd.parser.add_option(
            "--unignore",
            dest="unignore",
            action="store_true",
            help="un-ignore matching items' anomalies",
        )
        cmd.parser.add_option(
            "--mark-fake",
            dest="mark_fake",
            action="store_true",
            help="mark a number of plays as fake and subtract them",
        )
        cmd.parser.add_option(
            "--fake-count",
            dest="fake_count",
            type="int",
            default=0,
            help="number of fake plays to subtract (used with --mark-fake)",
        )
        cmd.parser.add_option(
            "--correct",
            dest="correct",
            type="int",
            default=0,
            help="correct play count to this value",
        )
        cmd.parser.add_option(
            "-n",
            "--limit",
            dest="limit",
            type="int",
            default=0,
            help="limit number of results shown",
        )
        cmd.parser.add_option(
            "-f",
            "--format",
            dest="format",
            default="",
            help="custom format string for output",
        )
        cmd.parser.add_option(
            "-i",
            "--interactive",
            dest="interactive",
            action="store_true",
            help="interactively review and resolve anomalies one by one",
        )
        cmd.parser.add_option(
            "--show-history",
            dest="show_history",
            action="store_true",
            help="show play history for matching items",
        )
        cmd.parser.add_option(
            "--purge-history",
            dest="purge_history",
            action="store_true",
            help="purge old play history (older than retention days)",
        )
        cmd.parser.add_option(
            "--rebuild-history",
            dest="rebuild_history",
            action="store_true",
            help="rebuild daily play statistics from existing data",
        )
        cmd.func = self._anomalies_cmd
        return [cmd]

    def _get_play_count(self, item: Item) -> int:
        """Get the play count from an item using the configured field."""
        field = self.config["play_count_field"].as_str()
        return int(item.get(field, 0) or 0)

    def _get_last_played(self, item: Item) -> int:
        """Get the last played timestamp from an item."""
        field = self.config["last_played_field"].as_str()
        value = item.get(field, 0)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return 0
        return int(value or 0)

    def _set_play_count(self, item: Item, value: int) -> None:
        """Set the play count on an item."""
        field = self.config["play_count_field"].as_str()
        item[field] = value
        item.store()

    def _get_device_id(self) -> str:
        """Get a unique identifier for the current device.

        Resolution order:
        1. Explicit ``device_id`` config value
        2. ``BEETS_DEVICE_ID`` environment variable
        3. Auto-generated ``username@hostname`` pattern
        4. Fallback: ``unknown-<process_id>`` when hostname/username unavailable
        """
        try:
            device_id = self.config["device_id"].as_str()
            if device_id:
                return device_id
        except Exception:
            pass

        try:
            env_device_id = os.environ.get("BEETS_DEVICE_ID", "")
            if env_device_id:
                return env_device_id
        except Exception:
            pass

        try:
            hostname = socket.gethostname()
        except Exception:
            hostname = None

        try:
            username = os.environ.get("USER", "") or os.environ.get("LOGNAME", "")
        except Exception:
            username = None

        if hostname and username:
            return f"{username}@{hostname}"
        if hostname:
            return hostname
        if username:
            return f"{username}@unknown"

        return f"unknown-{os.getpid()}"

    def _get_device_name(self) -> str:
        """Get the display name for the current device.

        Resolution order:
        1. Explicit ``device_name`` config value
        2. ``BEETS_DEVICE_NAME`` environment variable
        3. System hostname
        4. Fallback: ``Unknown Device`` when nothing is available
        """
        try:
            device_name = self.config["device_name"].as_str()
            if device_name:
                return device_name
        except Exception:
            pass

        try:
            env_device_name = os.environ.get("BEETS_DEVICE_NAME", "")
            if env_device_name:
                return env_device_name
        except Exception:
            pass

        try:
            return socket.gethostname()
        except Exception:
            return "Unknown Device"

    def _on_library_opened(self, lib: Library) -> None:
        """Handle library opened event: create history table and load state."""
        self._ensure_play_history_table(lib)
        self._load_previous_play_counts(lib)

    def _ensure_play_history_table(self, lib: Library) -> None:
        """Create the play history table if it doesn't exist."""
        create_sql = f"""
        CREATE TABLE IF NOT EXISTS {PLAY_HISTORY_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            play_time INTEGER NOT NULL,
            play_count INTEGER NOT NULL DEFAULT 0,
            play_count_delta INTEGER NOT NULL DEFAULT 0,
            device_id TEXT,
            device_name TEXT,
            source TEXT,
            duration REAL,
            FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
        )
        """
        db = lib._db  # type: ignore[attr-defined]
        try:
            db.mutate(create_sql)
            index_sql = f"""
            CREATE INDEX IF NOT EXISTS {PLAY_HISTORY_TABLE}_by_item_time
            ON {PLAY_HISTORY_TABLE} (item_id, play_time DESC)
            """
            db.mutate(index_sql)
        except Exception as e:
            self._log.debug("Failed to create play history table: {}", e)

    def _load_previous_play_counts(self, lib: Library) -> None:
        """Load current play counts to detect changes."""
        if not self.config["enable_history_tracking"].get(bool):
            return
        field = self.config["play_count_field"].as_str()
        for item in lib.items():
            self._previous_play_counts[item.id] = int(item.get(field, 0) or 0)

    def _on_database_change(self, lib: Library, model) -> None:
        """Handle database change event: track play count updates."""
        if not self.config["enable_history_tracking"].get(bool):
            return
        if model._table != "items":
            return
        item = model
        play_count = self._get_play_count(item)
        previous = self._previous_play_counts.get(item.id, 0)
        delta = play_count - previous
        if delta > 0:
            self._record_play(lib, item, play_count, delta)
        self._previous_play_counts[item.id] = play_count

    def _record_play(
        self,
        lib: Library,
        item: Item,
        play_count: int,
        delta: int,
        source: str = "database",
        device_id: str | None = None,
        device_name: str | None = None,
        play_time: int | None = None,
    ) -> None:
        """Record a play event to the history table."""
        timestamp = play_time if play_time else int(time.time())
        dev_id = device_id if device_id else self._get_device_id()
        dev_name = device_name if device_name else self._get_device_name()
        insert_sql = f"""
        INSERT INTO {PLAY_HISTORY_TABLE}
        (item_id, play_time, play_count, play_count_delta,
         device_id, device_name, source, duration)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        db = lib._db  # type: ignore[attr-defined]
        try:
            db.mutate(
                insert_sql,
                (
                    item.id,
                    timestamp,
                    play_count,
                    delta,
                    dev_id,
                    dev_name,
                    source,
                    float(item.length or 0),
                ),
            )
        except Exception as e:
            self._log.debug("Failed to record play history: {}", e)

    def record_play(
        self,
        item: Item,
        delta: int = 1,
        source: str = "api",
        device_id: str | None = None,
        device_name: str | None = None,
        play_time: int | None = None,
    ) -> bool:
        """Record a play event from an external source.

        This is the public API for other plugins or integrations to report
        play events with device and timestamp information for accurate
        cross-device anomaly detection.

        Args:
            item: The item that was played.
            delta: Number of plays to record (default: 1).
            source: Identifier for the data source (e.g., "mpd", "lastfm", "api").
            device_id: Unique device identifier. If None, uses auto-detection.
            device_name: Human-readable device name. If None, uses auto-detection.
            play_time: Unix timestamp of the play event. If None, uses current time.

        Returns:
            True if the play was recorded successfully, False otherwise.

        Example::

            from beets.plugins import find_plugins

            plugin = next(
                p for p in find_plugins()
                if p.name == "playcountanomaly"
            )
            plugin.record_play(
                item,
                delta=1,
                source="my_player",
                device_id="phone-abc123",
                device_name="My iPhone",
            )
        """
        if delta <= 0:
            return False
        if not self.config["enable_history_tracking"].get(bool):
            return False

        lib = item._db  # type: ignore[attr-defined]
        if lib is None:
            return False
        play_count = self._get_play_count(item) + delta
        self._record_play(
            lib,
            item,
            play_count,
            delta,
            source=source,
            device_id=device_id,
            device_name=device_name,
            play_time=play_time,
        )
        return True

    def _get_play_history(
        self, lib: Library, item_id: int, days: int | None = None
    ) -> list[dict[str, Any]]:
        """Get play history for an item."""
        query = f"""
        SELECT * FROM {PLAY_HISTORY_TABLE}
        WHERE item_id = ?
        """
        params: Sequence[Any] = [item_id]
        if days:
            cutoff = int(time.time()) - days * 86400
            query += " AND play_time >= ?"
            params = [*list(params), cutoff]
        query += " ORDER BY play_time DESC"
        db = lib._db  # type: ignore[attr-defined]
        try:
            results = db.query(query, tuple(params))
            return [dict(row) for row in results]
        except Exception as e:
            self._log.debug("Failed to get play history: {}", e)
            return []

    def _get_daily_play_counts(
        self, history: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Aggregate play history by day.

        Returns a dict with date strings as keys and play count info as values.
        """
        daily: dict[str, dict[str, Any]] = {}
        for record in history:
            play_time = record.get("play_time", 0)
            date_str = time.strftime("%Y-%m-%d", time.localtime(play_time))
            delta = record.get("play_count_delta", 0)
            if date_str not in daily:
                daily[date_str] = {"count": 0, "devices": set(), "sources": set()}
            daily[date_str]["count"] += delta
            if record.get("device_id"):
                daily[date_str]["devices"].add(record["device_id"])
            if record.get("source"):
                daily[date_str]["sources"].add(record["source"])
        return daily

    def _get_item_play_history(self, item: Item) -> list[dict[str, Any]]:
        """Get play history from item's flex attribute or database."""
        history_str = item.get("play_history", "")
        if history_str:
            try:
                return json.loads(history_str)
            except (json.JSONDecodeError, ValueError):
                return []
        return []

    def _update_item_play_history(
        self, item: Item, history: list[dict[str, Any]]
    ) -> None:
        """Update item's play history flex attribute."""
        retention = self.config["history_retention_days"].get(int)
        cutoff = int(time.time()) - retention * 86400
        filtered = [h for h in history if h.get("play_time", 0) >= cutoff]
        try:
            item["play_history"] = json.dumps(filtered)
        except (TypeError, ValueError):
            pass

    def _purge_old_history(self, lib: Library) -> int:
        """Purge play history older than retention period."""
        retention = self.config["history_retention_days"].get(int)
        cutoff = int(time.time()) - retention * 86400
        delete_sql = f"""
        DELETE FROM {PLAY_HISTORY_TABLE}
        WHERE play_time < ?
        """
        db = lib._db  # type: ignore[attr-defined]
        try:
            db.mutate(delete_sql, (cutoff,))
            check_sql = f"""
            SELECT COUNT(*) as cnt FROM {PLAY_HISTORY_TABLE}
            WHERE play_time < ?
            """
            result = db.query(check_sql, (cutoff,))
            remaining = 0
            for row in result:
                remaining = row["cnt"]
                break
            return remaining
        except Exception as e:
            self._log.debug("Failed to purge history: {}", e)
            return -1

    def detect_anomalies(
        self, item: Item, stats: dict[str, Any], now: float | None = None
    ) -> list[Anomaly]:
        """Detect anomalies for a single item.

        Args:
            item: The item to analyze.
            stats: Pre-computed statistics about the library.
            now: Current timestamp (for testing).

        Returns:
            A list of Anomaly namedtuples.
        """
        if now is None:
            now = time.time()

        anomalies: list[Anomaly] = []
        play_count = self._get_play_count(item)
        min_plays = self.config["min_plays_for_anomaly"].get(int)

        if play_count <= 0:
            if self._check_sudden_zero(item, now):
                anomalies.append(
                    Anomaly(
                        type="sudden_zero",
                        score=1.0,
                        description=(
                            "song has been played recently but play count is zero"
                        ),
                    )
                )
            return anomalies

        if play_count < min_plays:
            return anomalies

        zscore_anomaly = self._check_zscore_deviation(play_count, stats)
        if zscore_anomaly:
            anomalies.append(zscore_anomaly)

        burst_anomaly = self._check_new_song_burst(item, play_count, stats, now)
        if burst_anomaly:
            anomalies.append(burst_anomaly)

        impossible_anomaly = self._check_impossible_count(item, play_count, now)
        if impossible_anomaly:
            anomalies.append(impossible_anomaly)

        lib = item._db  # type: ignore[attr-defined]
        history: list[dict[str, Any]] = []
        if lib:
            history = self._get_play_history(lib, item.id, days=90)

        daily_spike_anomaly = self._check_daily_spike(item, history, now)
        if daily_spike_anomaly:
            anomalies.append(daily_spike_anomaly)

        concurrent_anomaly = self._check_concurrent_play(item, history, now)
        if concurrent_anomaly:
            anomalies.append(concurrent_anomaly)

        device_anomaly = self._check_device_anomaly(item, history, stats, now)
        if device_anomaly:
            anomalies.append(device_anomaly)

        return anomalies

    def _check_zscore_deviation(
        self, play_count: int, stats: dict[str, Any]
    ) -> Anomaly | None:
        """Check if play count deviates significantly from the mean."""
        threshold = self.config["zscore_threshold"].get(float)
        stddev = stats.get("stddev", 0)

        if stddev == 0:
            return None

        mean = stats.get("mean", 0)
        zscore = (play_count - mean) / stddev

        if zscore >= threshold:
            return Anomaly(
                type="zscore_deviation",
                score=zscore / threshold,
                description=(
                    f"play count {play_count} is {zscore:.1f} standard deviations "
                    f"above the mean ({mean:.1f})"
                ),
            )
        return None

    def _check_new_song_burst(
        self, item: Item, play_count: int, stats: dict[str, Any], now: float
    ) -> Anomaly | None:
        """Check if a newly added song has an unusually high play frequency."""
        new_song_days = self.config["new_song_days"].get(int)
        burst_ratio = self.config["new_song_burst_ratio"].get(float)

        added = item.added
        if not added:
            return None

        days_since_added = (now - added) / 86400.0
        if days_since_added <= 0 or days_since_added > new_song_days:
            return None

        plays_per_day = play_count / days_since_added
        median_per_day = stats.get("median_per_day", 0)

        if median_per_day <= 0:
            return None

        ratio = plays_per_day / median_per_day
        if ratio >= burst_ratio:
            return Anomaly(
                type="new_song_burst",
                score=ratio / burst_ratio,
                description=(
                    f"new song ({days_since_added:.1f} days old) has "
                    f"{plays_per_day:.1f} plays/day, "
                    f"{ratio:.1f}x the median rate ({median_per_day:.2f}/day)"
                ),
            )
        return None

    def _check_impossible_count(
        self, item: Item, play_count: int, now: float
    ) -> Anomaly | None:
        """Check if the play count is physically impossible.

        This detects cases where the song couldn't possibly have been
        played that many times given its length and the time available.
        This can indicate concurrent/duplicate counting.
        """
        threshold = self.config["impossible_ratio_threshold"].get(float)

        length = item.length
        if not length or length <= 0:
            return None

        added = item.added or now
        available_seconds = now - added
        if available_seconds <= 0:
            return None

        min_required_seconds = play_count * length
        ratio = min_required_seconds / available_seconds

        if ratio >= threshold:
            return Anomaly(
                type="impossible_count",
                score=ratio / threshold,
                description=(
                    f"play count {play_count} requires {min_required_seconds:.0f}s "
                    f"but only {available_seconds:.0f}s available "
                    f"(ratio: {ratio:.2f}x)"
                ),
            )
        return None

    def _check_sudden_zero(self, item: Item, now: float) -> bool:
        """Check if a song has recent plays but zero play count.

        This suggests the play count may have been reset (e.g., in shared
        libraries or after sync issues).
        """
        sudden_zero_days = self.config["sudden_zero_days"].get(int)
        last_played = self._get_last_played(item)

        if not last_played:
            return False

        days_since_played = (now - last_played) / 86400.0
        return days_since_played <= sudden_zero_days

    def _check_daily_spike(
        self, item: Item, history: list[dict[str, Any]], now: float
    ) -> Anomaly | None:
        """Check for single-day play count spikes far above average.

        This detects anomalies where a song is played an unusual number
        of times in a single day, which could indicate:
        - Accidental repeat plays
        - Automatic playback issues
        - Fraudulent play counting
        """
        if not history:
            return None

        threshold = self.config["daily_spike_threshold"].get(float)
        min_plays = self.config["daily_spike_min_plays"].get(int)

        daily = self._get_daily_play_counts(history)
        if not daily:
            return None

        counts = [v["count"] for v in daily.values() if v["count"] > 0]
        if len(counts) < 3:
            return None

        mean_daily = sum(counts) / len(counts)
        if mean_daily <= 0:
            return None

        max_daily = max(counts)
        if max_daily < min_plays:
            return None

        ratio = max_daily / mean_daily
        if ratio >= threshold:
            max_date = next(k for k, v in daily.items() if v["count"] == max_daily)
            return Anomaly(
                type="daily_spike",
                score=ratio / threshold,
                description=(
                    f"single-day spike of {max_daily} plays on {max_date}, "
                    f"{ratio:.1f}x the daily average ({mean_daily:.1f})"
                ),
            )
        return None

    def _check_concurrent_play(
        self, item: Item, history: list[dict[str, Any]], now: float
    ) -> Anomaly | None:
        """Check for concurrent plays across devices suggesting duplicate counting.

        This detects cases where the same song appears to be played
        multiple times within a short time window from different devices,
        which is physically impossible for a single user.
        """
        if not history:
            return None

        window = self.config["concurrent_window_seconds"].get(int)
        min_plays = self.config["concurrent_min_plays"].get(int)
        min_devices = self.config["concurrent_device_count"].get(int)

        sorted_history = sorted(history, key=lambda x: x.get("play_time", 0))

        for i, base_record in enumerate(sorted_history):
            window_end = base_record.get("play_time", 0) + window
            window_plays = [base_record]
            devices = {base_record.get("device_id", "unknown")}

            for j in range(i + 1, len(sorted_history)):
                other = sorted_history[j]
                if other.get("play_time", 0) > window_end:
                    break
                window_plays.append(other)
                devices.add(other.get("device_id", "unknown"))

            total_plays = sum(r.get("play_count_delta", 0) for r in window_plays)
            if (
                total_plays >= min_plays
                and len(devices) >= min_devices
            ):
                device_list = ", ".join(sorted(devices))
                return Anomaly(
                    type="concurrent_play",
                    score=min(total_plays / min_plays, len(devices) / min_devices),
                    description=(
                        f"{total_plays} plays within {window}s from "
                        f"{len(devices)} devices ({device_list}), "
                        f"suggesting concurrent duplicate counting"
                    ),
                )

        return None

    def _check_device_anomaly(
        self,
        item: Item,
        history: list[dict[str, Any]],
        stats: dict[str, Any],
        now: float,
    ) -> Anomaly | None:
        """Check for unusual play patterns from specific devices.

        This detects cases where a new or rare device accounts for a
        disproportionate share of plays, which could indicate:
        - Shared library sync issues
        - Test/automation accounts
        - Fraudulent playback from unexpected sources
        """
        if not history:
            return None

        threshold = self.config["device_anomaly_threshold"].get(float)
        total_plays = sum(r.get("play_count_delta", 0) for r in history)
        if total_plays < self.config["min_plays_for_anomaly"].get(int):
            return None

        device_plays: dict[str, int] = {}
        for record in history:
            device = record.get("device_id", "unknown")
            device_plays[device] = device_plays.get(device, 0) + record.get(
                "play_count_delta", 0
            )

        if len(device_plays) < 2:
            return None

        for device, plays in device_plays.items():
            ratio = (plays / total_plays) * len(device_plays)
            if ratio >= threshold:
                return Anomaly(
                    type="device_anomaly",
                    score=ratio / threshold,
                    description=(
                        f"device '{device}' accounts for {plays}/{total_plays} "
                        f"plays ({plays/total_plays*100:.1f}%), "
                        f"{ratio:.1f}x the expected share"
                    ),
                )

        return None

    def compute_stats(self, lib: Library) -> dict[str, Any]:
        """Compute library-wide statistics needed for anomaly detection."""
        play_counts: list[int] = []
        per_day_rates: list[float] = []
        now = time.time()

        for item in lib.items():
            play_count = self._get_play_count(item)
            if play_count > 0:
                play_counts.append(play_count)

                added = item.added
                if added:
                    days_since_added = (now - added) / 86400.0
                    if days_since_added > 0:
                        per_day_rates.append(play_count / days_since_added)

        if not play_counts:
            return {
                "mean": 0,
                "stddev": 0,
                "median": 0,
                "median_per_day": 0,
                "total_items": 0,
            }

        mean = sum(play_counts) / len(play_counts)
        stddev = _stddev([float(pc) for pc in play_counts], mean)
        median = _median([float(pc) for pc in play_counts])
        median_per_day = _median(per_day_rates) if per_day_rates else 0

        return {
            "mean": mean,
            "stddev": stddev,
            "median": median,
            "median_per_day": median_per_day,
            "total_items": len(play_counts),
        }

    def scan_library(self, lib: Library) -> tuple[int, int]:
        """Scan the entire library and update anomaly information.

        Returns:
            A tuple of (total_scanned, total_anomalous).
        """
        stats = self.compute_stats(lib)
        total_scanned = 0
        total_anomalous = 0

        for item in lib.items():
            anomalies = self.detect_anomalies(item, stats)
            total_scanned += 1

            if anomalies:
                total_anomalous += 1
                max_score = max(a.score for a in anomalies)
                types_str = ",".join(sorted({a.type for a in anomalies}))
                descriptions = "; ".join(a.description for a in anomalies)

                item["anomaly_score"] = max_score
                item["anomaly_types"] = types_str
                if not item.get("anomaly_reviewed"):
                    item["anomaly_reviewed"] = False
                item["anomaly_notes"] = descriptions
            else:
                if item.get("anomaly_score"):
                    item["anomaly_score"] = 0.0
                if item.get("anomaly_types"):
                    item["anomaly_types"] = ""
                if item.get("anomaly_notes"):
                    item["anomaly_notes"] = ""

            item.store()

        return total_scanned, total_anomalous

    def _iter_anomalous_items(
        self,
        lib: Library,
        show_all: bool = False,
        filter_type: str = "",
        query: list[str] | None = None,
    ) -> Iterator[tuple[Item, list[str]]]:
        """Iterate over items with anomalies.

        Yields:
            Tuples of (item, anomaly_types_list).
        """
        items = lib.items(query if query else None)

        for item in items:
            anomaly_types_str = item.get("anomaly_types", "")
            if not anomaly_types_str:
                continue

            if not show_all and item.get("anomaly_reviewed", False):
                if item.get("anomaly_resolution") == RESOLUTION_IGNORED:
                    continue

            types_list = [t for t in anomaly_types_str.split(",") if t]
            if filter_type and filter_type not in types_list:
                continue

            yield item, types_list

    def _anomalies_cmd(self, lib: Library, opts, args: list[str]) -> None:
        """Handle the 'anomalies' CLI command."""
        query = args if args else None

        if opts.rescan:
            self._log.info("Scanning library for play count anomalies...")
            total, anomalous = self.scan_library(lib)
            self._log.info(
                "Scanned {} items, found {} with anomalies", total, anomalous
            )
            return

        if opts.purge_history:
            self._handle_purge_history(lib)
            return

        if opts.rebuild_history:
            self._handle_rebuild_history(lib)
            return

        if opts.show_history:
            self._show_play_history(lib, opts, query)
            return

        if opts.ignore or opts.unignore:
            self._handle_ignore(lib, opts, query)
            return

        if opts.mark_fake:
            self._handle_mark_fake(lib, opts, query)
            return

        if opts.correct:
            self._handle_correct(lib, opts, query)
            return

        if opts.interactive:
            self._interactive_review(lib, opts, query)
            return

        self._list_anomalies(lib, opts, query)

    def _handle_ignore(
        self, lib: Library, opts, query: list[str] | None
    ) -> None:
        """Handle marking anomalies as ignored or un-ignored."""
        count = 0
        items = list(lib.items(query if query else None))

        for item in items:
            if not item.get("anomaly_types"):
                continue

            if opts.ignore:
                item["anomaly_reviewed"] = True
                item["anomaly_resolution"] = RESOLUTION_IGNORED
            else:
                item["anomaly_reviewed"] = False
                item["anomaly_resolution"] = ""

            item.store()
            count += 1

        action = "ignored" if opts.ignore else "un-ignored"
        ui.print_(f"Marked {count} item(s) as {action}")

    def _handle_mark_fake(
        self, lib: Library, opts, query: list[str] | None
    ) -> None:
        """Handle marking plays as fake and subtracting them."""
        fake_count = opts.fake_count
        if fake_count <= 0:
            raise ui.UserError(
                "Please specify --fake-count with --mark-fake"
            )

        count = 0
        items = list(lib.items(query if query else None))

        with lib.transaction():
            for item in items:
                current = self._get_play_count(item)
                if current <= 0:
                    continue

                new_count = max(0, current - fake_count)
                current_fake = int(item.get("fake_play_count", 0) or 0)

                self._set_play_count(item, new_count)
                item["fake_play_count"] = current_fake + fake_count
                item["anomaly_reviewed"] = True
                item["anomaly_resolution"] = RESOLUTION_FAKE
                item.store()

                count += 1

        ui.print_(
            f"Marked {fake_count} fake plays on {count} item(s), "
            f"play counts reduced accordingly"
        )

    def _handle_correct(
        self, lib: Library, opts, query: list[str] | None
    ) -> None:
        """Handle correcting play counts."""
        new_value = opts.correct
        if new_value < 0:
            raise ui.UserError("Play count cannot be negative")

        count = 0
        items = list(lib.items(query if query else None))

        with lib.transaction():
            for item in items:
                old_value = self._get_play_count(item)
                if old_value == new_value:
                    continue

                self._set_play_count(item, new_value)
                item["anomaly_reviewed"] = True
                item["anomaly_resolution"] = RESOLUTION_CORRECTED
                item.store()

                count += 1

        ui.print_(f"Corrected play count to {new_value} on {count} item(s)")

    def _handle_purge_history(self, lib: Library) -> None:
        """Purge old play history records."""
        retention = self.config["history_retention_days"].get(int)
        self._log.info(
            "Purging play history older than {} days...", retention
        )
        remaining = self._purge_old_history(lib)
        if remaining >= 0:
            self._log.info(
                "Purge complete. {} old records remain in database.", remaining
            )
        else:
            self._log.error("Failed to purge history")

    def _handle_rebuild_history(self, lib: Library) -> None:
        """Rebuild daily play statistics from history."""
        self._log.info("Rebuilding daily play statistics...")
        count = 0
        for item in lib.items():
            history = self._get_play_history(lib, item.id)
            if history:
                daily = self._get_daily_play_counts(history)
                daily_serializable = {
                    k: {
                        **v,
                        "devices": list(v["devices"]),
                        "sources": list(v["sources"]),
                    }
                    for k, v in daily.items()
                }
                try:
                    item["daily_play_stats"] = json.dumps(daily_serializable)
                    item.store()
                    count += 1
                except (TypeError, ValueError):
                    pass
        self._log.info("Rebuilt statistics for {} items", count)

    def _show_play_history(
        self, lib: Library, opts, query: list[str] | None
    ) -> None:
        """Show detailed play history for matching items."""
        items = list(lib.items(query if query else None))
        if not items:
            ui.print_("No items found matching query.")
            return

        for item in items:
            history = self._get_play_history(lib, item.id, days=30)
            play_count = self._get_play_count(item)
            ui.print_()
            ui.print_(f"=== {item.artist} - {item.title} ===")
            ui.print_(f"Total plays: {play_count}")
            ui.print_(f"History records (last 30 days): {len(history)}")

            if history:
                ui.print_()
                ui.print_(
                    f"{'Date':<12} {'Time':<10} {'Delta':>6} "
                    f"{'Device':<20} {'Source'}"
                )
                ui.print_("-" * 70)
                for record in history[:20]:
                    play_time = record.get("play_time", 0)
                    date_str = time.strftime("%Y-%m-%d", time.localtime(play_time))
                    time_str = time.strftime("%H:%M:%S", time.localtime(play_time))
                    delta = record.get("play_count_delta", 0)
                    device = record.get("device_name", "unknown")[:20]
                    source = record.get("source", "unknown")
                    ui.print_(
                        f"{date_str:<12} {time_str:<10} {delta:>6} "
                        f"{device:<20} {source}"
                    )
                if len(history) > 20:
                    ui.print_(f"... and {len(history) - 20} more records")

                daily = self._get_daily_play_counts(history)
                if daily:
                    ui.print_()
                    ui.print_("Daily breakdown:")
                    for date in sorted(daily.keys(), reverse=True)[:7]:
                        info = daily[date]
                        devices = ", ".join(sorted(info["devices"]))[:30]
                        device_count = len(info["devices"])
                        ui.print_(
                            f"  {date}: {info['count']} plays from "
                            f"{device_count} device(s)"
                        )
                        if devices:
                            ui.print_(f"    Devices: {devices}")
            else:
                ui.print_("No play history recorded for this item.")
            ui.print_()

    def _interactive_review(
        self, lib: Library, opts, query: list[str] | None
    ) -> None:
        """Interactively review and resolve anomalies one by one."""
        show_all = opts.show_all
        filter_type = opts.filter_type

        anomalies_list: list[tuple[Item, list[str], float]] = []
        for item, types_list in self._iter_anomalous_items(
            lib, show_all, filter_type, query
        ):
            score = float(item.get("anomaly_score", 0) or 0)
            anomalies_list.append((item, types_list, score))

        anomalies_list.sort(key=lambda x: -x[2])

        if not anomalies_list:
            ui.print_("No anomalies to review.")
            return

        ui.print_(
            f"Found {len(anomalies_list)} anomalies for interactive review."
        )
        ui.print_(
            "Commands: [i]gnore, [m]ark-fake, [c]orrect, "
            "[s]kip, [h]istory, [q]uit"
        )
        ui.print_("-" * 70)

        for i, (item, types_list, score) in enumerate(anomalies_list, 1):
            play_count = self._get_play_count(item)
            resolution = item.get("anomaly_resolution", "") or ""
            reviewed = item.get("anomaly_reviewed", False)
            status = f" [reviewed: {resolution}]" if reviewed else ""

            ui.print_()
            ui.print_(
                f"[{i}/{len(anomalies_list)}] {item.artist} - {item.title}"
            )
            types_str = ", ".join(types_list)
            ui.print_(
                f"    Plays: {play_count} | Score: {score:.2f} | "
                f"Types: {types_str}{status}"
            )
            notes = item.get("anomaly_notes", "")
            if notes:
                ui.print_(f"    Details: {notes}")

            while True:
                try:
                    choice = ui.input_(
                        "Action? (i=ignore, m=mark-fake, c=correct, "
                        "s=skip, h=history, q=quit) [s]: ",
                    ).lower()
                    if not choice:
                        choice = "s"
                except EOFError:
                    ui.print_()
                    return

                if choice == "q":
                    ui.print_("Quitting review.")
                    return
                elif choice == "s":
                    ui.print_("Skipped.")
                    break
                elif choice == "i":
                    item["anomaly_reviewed"] = True
                    item["anomaly_resolution"] = RESOLUTION_IGNORED
                    item.store()
                    ui.print_("Marked as ignored.")
                    break
                elif choice == "m":
                    try:
                        fake_count = int(
                            ui.input_("Number of fake plays to subtract: ")
                        )
                        if fake_count <= 0:
                            ui.print_("Please enter a positive number.")
                            continue
                    except (ValueError, EOFError):
                        ui.print_("Invalid number, skipping.")
                        break

                    with lib.transaction():
                        new_count = max(0, play_count - fake_count)
                        current_fake = int(item.get("fake_play_count", 0) or 0)
                        self._set_play_count(item, new_count)
                        item["fake_play_count"] = current_fake + fake_count
                        item["anomaly_reviewed"] = True
                        item["anomaly_resolution"] = RESOLUTION_FAKE
                        item.store()
                    ui.print_(
                        f"Marked {fake_count} fake plays. New count: {new_count}"
                    )
                    break
                elif choice == "c":
                    try:
                        new_value = int(ui.input_("Corrected play count: "))
                        if new_value < 0:
                            ui.print_("Play count cannot be negative.")
                            continue
                    except (ValueError, EOFError):
                        ui.print_("Invalid number, skipping.")
                        break

                    with lib.transaction():
                        self._set_play_count(item, new_value)
                        item["anomaly_reviewed"] = True
                        item["anomaly_resolution"] = RESOLUTION_CORRECTED
                        item.store()
                    ui.print_(f"Corrected play count to {new_value}")
                    break
                elif choice == "h":
                    history = self._get_play_history(lib, item.id, days=30)
                    ui.print_()
                    ui.print_(f"Recent play history ({len(history)} records):")
                    for record in history[:10]:
                        play_time = record.get("play_time", 0)
                        date_str = time.strftime(
                            "%Y-%m-%d %H:%M", time.localtime(play_time)
                        )
                        delta = record.get("play_count_delta", 0)
                        device = record.get("device_name", "unknown")
                        ui.print_(
                            f"  {date_str}: +{delta} from {device}"
                        )
                    ui.print_()
                else:
                    ui.print_("Invalid choice. Please try again.")

        ui.print_()
        ui.print_("Review complete!")

    def _list_anomalies(
        self, lib: Library, opts, query: list[str] | None
    ) -> None:
        """List anomalies matching the criteria."""
        show_all = opts.show_all
        filter_type = opts.filter_type
        sort_by = opts.sort_by
        limit = opts.limit

        anomalies_list: list[tuple[Item, list[str], float]] = []
        for item, types_list in self._iter_anomalous_items(
            lib, show_all, filter_type, query
        ):
            score = float(item.get("anomaly_score", 0) or 0)
            anomalies_list.append((item, types_list, score))

        if sort_by == "type":
            anomalies_list.sort(key=lambda x: (",".join(x[1]), -x[2]))
        else:
            anomalies_list.sort(key=lambda x: -x[2])

        if limit > 0:
            anomalies_list = anomalies_list[:limit]

        if not anomalies_list:
            ui.print_("No anomalies found.")
            return

        ui.print_(
            f"Found {len(anomalies_list)} item(s) with play count anomalies:"
        )
        ui.print_("-" * 70)

        for i, (item, types_list, score) in enumerate(anomalies_list, 1):
            play_count = self._get_play_count(item)
            resolution = item.get("anomaly_resolution", "") or ""
            status = ""
            if item.get("anomaly_reviewed", False):
                status = f" [reviewed: {resolution}]"

            types_str = ", ".join(types_list)
            ui.print_(
                f"{i}. {item.artist} - {item.title} "
                f"({play_count} plays, score: {score:.2f}){status}"
            )
            ui.print_(f"   Types: {types_str}")
            notes = item.get("anomaly_notes", "")
            if notes:
                ui.print_(f"   Details: {notes}")
            ui.print_()

        ui.print_(f"Total: {len(anomalies_list)} anomalies")
        ui.print_()
        ui.print_("Use --ignore / --unignore to manage anomaly status")
        ui.print_("Use --mark-fake --fake-count N to subtract fake plays")
        ui.print_("Use --correct N to set a corrected play count")
        ui.print_("Use --rescan to re-analyze the library")
        ui.print_("Use -i / --interactive for interactive review")
        ui.print_("Use --show-history to view detailed play history")
        ui.print_("Use --purge-history to clean old history records")
        ui.print_("Use --rebuild-history to rebuild daily statistics")
