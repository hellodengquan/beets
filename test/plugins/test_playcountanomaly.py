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
# The above copyright notice and this permission notice
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
# OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE
# USE OR OTHER DEALINGS IN THE SOFTWARE.

import time

import pytest

from beets.library import Item
from beets.test.helper import IOMixin, PluginMixin, TestHelper
from beetsplug.playcountanomaly import PlayCountAnomalyPlugin, _median, _stddev


class TestPlayCountAnomalyPlugin(PluginMixin, TestHelper, IOMixin):
    plugin = "playcountanomaly"

    @pytest.fixture(autouse=True)
    def setup(self):
        self.setup_beets()
        try:
            yield
        finally:
            self.teardown_beets()

    def _create_item_with_plays(self, play_count, **kwargs):
        """Helper to create an item with a play count."""
        item = self.create_item(
            play_count=play_count,
            **kwargs
        )
        item.add(self.lib)
        return item

    def test_median_empty(self):
        assert _median([]) == 0.0

    def test_median_odd(self):
        assert _median([1.0, 3.0, 2.0]) == 2.0

    def test_median_even(self):
        assert _median([1.0, 2.0, 3.0, 4.0]) == 2.5

    def test_stddev_single_value(self):
        assert _stddev([5.0], 5.0) == 0.0

    def test_stddev_values(self):
        values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        mean = sum(values) / len(values)
        assert _stddev(values, mean) == pytest.approx(2.0)

    def test_detect_zscore_deviation(self):
        plugin = PlayCountAnomalyPlugin()
        plugin.config["zscore_threshold"] = 2.0
        plugin.config["min_plays_for_anomaly"] = 1

        stats = {"mean": 10.0, "stddev": 5.0}
        item = Item(title="test", play_count=25)
        now = time.time()

        anomalies = plugin.detect_anomalies(item, stats, now=now)
        types = [a.type for a in anomalies]
        assert "zscore_deviation" in types

    def test_detect_no_zscore_deviation(self):
        plugin = PlayCountAnomalyPlugin()
        plugin.config["zscore_threshold"] = 3.0
        plugin.config["min_plays_for_anomaly"] = 1

        stats = {"mean": 10.0, "stddev": 5.0}
        item = Item(title="test", play_count=12)
        now = time.time()

        anomalies = plugin.detect_anomalies(item, stats, now=now)
        types = [a.type for a in anomalies]
        assert "zscore_deviation" not in types

    def test_detect_impossible_count(self):
        plugin = PlayCountAnomalyPlugin()
        plugin.config["impossible_ratio_threshold"] = 1.0
        plugin.config["min_plays_for_anomaly"] = 1

        now = time.time()
        added = now - 100

        item = Item(title="test", play_count=10, length=30.0, added=added)
        stats = {"mean": 0, "stddev": 0}

        anomalies = plugin.detect_anomalies(item, stats, now=now)
        types = [a.type for a in anomalies]
        assert "impossible_count" in types

    def test_detect_no_impossible_count(self):
        plugin = PlayCountAnomalyPlugin()
        plugin.config["impossible_ratio_threshold"] = 2.0
        plugin.config["min_plays_for_anomaly"] = 1

        now = time.time()
        added = now - 1000

        item = Item(title="test", play_count=2, length=30.0, added=added)
        stats = {"mean": 0, "stddev": 0}

        anomalies = plugin.detect_anomalies(item, stats, now=now)
        types = [a.type for a in anomalies]
        assert "impossible_count" not in types

    def test_detect_new_song_burst(self):
        plugin = PlayCountAnomalyPlugin()
        plugin.config["new_song_days"] = 7
        plugin.config["new_song_burst_ratio"] = 2.0
        plugin.config["min_plays_for_anomaly"] = 1

        now = time.time()
        added = now - 86400

        item = Item(title="test", play_count=10, added=added)
        stats = {"mean": 0, "stddev": 0, "median_per_day": 1.0}

        anomalies = plugin.detect_anomalies(item, stats, now=now)
        types = [a.type for a in anomalies]
        assert "new_song_burst" in types

    def test_detect_no_new_song_burst_old_song(self):
        plugin = PlayCountAnomalyPlugin()
        plugin.config["new_song_days"] = 7
        plugin.config["new_song_burst_ratio"] = 2.0
        plugin.config["min_plays_for_anomaly"] = 1

        now = time.time()
        added = now - 30 * 86400

        item = Item(title="test", play_count=100, added=added)
        stats = {"mean": 0, "stddev": 0, "median_per_day": 1.0}

        anomalies = plugin.detect_anomalies(item, stats, now=now)
        types = [a.type for a in anomalies]
        assert "new_song_burst" not in types

    def test_detect_sudden_zero(self):
        plugin = PlayCountAnomalyPlugin()
        plugin.config["sudden_zero_days"] = 30

        now = time.time()
        last_played = now - 86400

        item = Item(title="test", play_count=0, last_played=last_played)
        stats = {"mean": 0, "stddev": 0}

        anomalies = plugin.detect_anomalies(item, stats, now=now)
        types = [a.type for a in anomalies]
        assert "sudden_zero" in types

    def test_detect_no_sudden_zero_long_ago(self):
        plugin = PlayCountAnomalyPlugin()
        plugin.config["sudden_zero_days"] = 30

        now = time.time()
        last_played = now - 60 * 86400

        item = Item(title="test", play_count=0, last_played=last_played)
        stats = {"mean": 0, "stddev": 0}

        anomalies = plugin.detect_anomalies(item, stats, now=now)
        types = [a.type for a in anomalies]
        assert "sudden_zero" not in types

    def test_compute_stats(self):
        plugin = PlayCountAnomalyPlugin()

        self._create_item_with_plays(5, title="a")
        self._create_item_with_plays(10, title="b")
        self._create_item_with_plays(15, title="c")

        stats = plugin.compute_stats(self.lib)

        assert stats["total_items"] == 3
        assert stats["mean"] == 10.0
        assert stats["median"] == 10.0
        assert stats["stddev"] > 0

    def test_scan_library(self):
        plugin = PlayCountAnomalyPlugin()

        for i in range(20):
            self._create_item_with_plays(5, title=f"normal_{i}")
        self._create_item_with_plays(100, title="outlier")

        total, anomalous = plugin.scan_library(self.lib)

        assert total == 21
        assert anomalous >= 1

    def test_rescan_command(self):
        for i in range(10):
            self._create_item_with_plays(5, title=f"song_{i}")
        self._create_item_with_plays(50, title="high")

        out = self.run_with_output("anomalies", "--rescan")

        assert "Scanned" in out
        assert "found" in out

    def test_list_anomalies(self):
        for i in range(10):
            self._create_item_with_plays(5, title=f"song_{i}")
        self._create_item_with_plays(50, title="outlier")

        self.run_with_output("anomalies", "--rescan")

        out = self.run_with_output("anomalies")

        assert "anomalies" in out
        assert "outlier" in out

    def test_ignore_anomaly(self):
        for i in range(5):
            self._create_item_with_plays(5, title=f"song_{i}")
        self._create_item_with_plays(50, title="outlier")

        self.run_with_output("anomalies", "--rescan")

        out_before = self.run_with_output("anomalies")
        assert "outlier" in out_before

        self.run_with_output("anomalies", "--ignore", "outlier")

        out_after = self.run_with_output("anomalies")
        assert "outlier" not in out_after

    def test_unignore_anomaly(self):
        for i in range(5):
            self._create_item_with_plays(5, title=f"song_{i}")
        self._create_item_with_plays(50, title="outlier")

        self.run_with_output("anomalies", "--rescan")
        self.run_with_output("anomalies", "--ignore", "outlier")

        out_ignored = self.run_with_output("anomalies")
        assert "outlier" not in out_ignored

        self.run_with_output("anomalies", "--unignore", "outlier")

        out_unignored = self.run_with_output("anomalies")
        assert "outlier" in out_unignored

    def test_mark_fake(self):
        item = self._create_item_with_plays(20, title="test_song")

        self.run_with_output(
            "anomalies", "--rescan"
        )

        self.run_with_output(
            "anomalies", "--mark-fake", "--fake-count", "5", "test_song"
        )

        item.load()
        assert int(item.get("play_count", 0)) == 15
        assert int(item.get("fake_play_count", 0)) == 5

    def test_correct_play_count(self):
        item = self._create_item_with_plays(20, title="test_song")

        self.run_with_output(
            "anomalies", "--correct", "10", "test_song"
        )

        item.load()
        assert int(item.get("play_count", 0)) == 10
        assert item.get("anomaly_resolution") == "corrected"

    def test_filter_by_type(self):
        for i in range(10):
            self._create_item_with_plays(5, title=f"song_{i}")

        now = time.time()
        added = now - 86400
        item = self.create_item(
            title="burst_song",
            play_count=20,
            added=added,
            length=200.0,
        )
        item.add(self.lib)

        self.run_with_output("anomalies", "--rescan")

        out = self.run_with_output("anomalies", "--type", "new_song_burst")
        assert "burst_song" in out

    def test_device_id_detection(self):
        plugin = PlayCountAnomalyPlugin()
        plugin.config["device_id"] = "test-device-123"
        plugin.config["device_name"] = "Test Device"

        assert plugin._get_device_id() == "test-device-123"
        assert plugin._get_device_name() == "Test Device"

    def test_device_id_auto_detect(self):
        plugin = PlayCountAnomalyPlugin()
        plugin.config["device_id"] = ""
        plugin.config["device_name"] = ""

        device_id = plugin._get_device_id()
        device_name = plugin._get_device_name()

        assert isinstance(device_id, str)
        assert len(device_id) > 0
        assert "@" in device_id
        assert isinstance(device_name, str)
        assert len(device_name) > 0

    def test_record_play_api(self):
        plugin = PlayCountAnomalyPlugin()
        plugin.config["enable_history_tracking"] = True

        item = self._create_item_with_plays(5, title="test_record")

        result = plugin.record_play(
            item,
            delta=1,
            source="test_api",
            device_id="test-device",
            device_name="Test Device",
        )
        assert result is True

        result_zero = plugin.record_play(item, delta=0)
        assert result_zero is False

        result_neg = plugin.record_play(item, delta=-1)
        assert result_neg is False

    def test_play_history_table_exists(self):
        plugin = PlayCountAnomalyPlugin()
        plugin._ensure_play_history_table(self.lib)

        db = self.lib._db
        result = db.query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("play_count_history",),
        )
        tables = [row["name"] for row in result]
        assert "play_count_history" in tables

    def test_daily_spike_detection(self):
        plugin = PlayCountAnomalyPlugin()
        plugin.config["daily_spike_threshold"] = 3.0
        plugin.config["daily_spike_min_plays"] = 5
        plugin.config["min_plays_for_anomaly"] = 1

        now = int(time.time())
        one_day = 86400

        history = []
        for i in range(10):
            day_offset = (i + 1) * one_day
            history.append({
                "play_time": now - day_offset,
                "play_count_delta": 2,
                "device_id": "device1",
                "source": "test",
            })

        spike_day = now - 5 * one_day
        for i in range(15):
            history.append({
                "play_time": spike_day + i * 60,
                "play_count_delta": 1,
                "device_id": "device1",
                "source": "test",
            })

        item = Item(title="spike_test", play_count=35)
        anomaly = plugin._check_daily_spike(item, history, float(now))

        assert anomaly is not None
        assert anomaly.type == "daily_spike"
        assert anomaly.score > 0
        assert "spike" in anomaly.description

    def test_no_daily_spike_normal(self):
        plugin = PlayCountAnomalyPlugin()
        plugin.config["daily_spike_threshold"] = 5.0
        plugin.config["daily_spike_min_plays"] = 5

        now = int(time.time())
        one_day = 86400

        history = []
        for i in range(14):
            day_offset = (i + 1) * one_day
            history.append({
                "play_time": now - day_offset,
                "play_count_delta": 2,
                "device_id": "device1",
                "source": "test",
            })

        item = Item(title="normal", play_count=28)
        anomaly = plugin._check_daily_spike(item, history, float(now))

        assert anomaly is None

    def test_concurrent_play_detection(self):
        plugin = PlayCountAnomalyPlugin()
        plugin.config["concurrent_window_seconds"] = 300
        plugin.config["concurrent_min_plays"] = 3
        plugin.config["concurrent_device_count"] = 2

        now = int(time.time())

        history = []
        devices = ["device_a", "device_b", "device_c"]
        for i, device in enumerate(devices):
            history.append({
                "play_time": now + i * 10,
                "play_count_delta": 2,
                "device_id": device,
                "source": "test",
            })

        item = Item(title="concurrent_test", play_count=10)
        anomaly = plugin._check_concurrent_play(item, history, float(now))

        assert anomaly is not None
        assert anomaly.type == "concurrent_play"
        assert "concurrent" in anomaly.description

    def test_no_concurrent_play_single_device(self):
        plugin = PlayCountAnomalyPlugin()
        plugin.config["concurrent_window_seconds"] = 300
        plugin.config["concurrent_min_plays"] = 3
        plugin.config["concurrent_device_count"] = 2

        now = int(time.time())

        history = []
        for i in range(5):
            history.append({
                "play_time": now + i * 10,
                "play_count_delta": 1,
                "device_id": "only_device",
                "source": "test",
            })

        item = Item(title="single_device", play_count=5)
        anomaly = plugin._check_concurrent_play(item, history, float(now))

        assert anomaly is None

    def test_device_anomaly_detection(self):
        plugin = PlayCountAnomalyPlugin()
        plugin.config["device_anomaly_threshold"] = 3.0
        plugin.config["min_plays_for_anomaly"] = 5

        now = int(time.time())
        one_day = 86400

        history = []
        for i in range(50):
            history.append({
                "play_time": now - i * one_day,
                "play_count_delta": 1,
                "device_id": "normal_device",
                "source": "test",
            })

        for i in range(50):
            history.append({
                "play_time": now - i * 3600,
                "play_count_delta": 1,
                "device_id": "suspicious_device",
                "source": "test",
            })

        item = Item(title="device_test", play_count=100)
        stats = {"mean": 10.0, "stddev": 5.0}
        anomaly = plugin._check_device_anomaly(item, history, stats, float(now))

        assert anomaly is not None
        assert anomaly.type == "device_anomaly"
        assert "device" in anomaly.description

    def test_no_device_anomaly_balanced(self):
        plugin = PlayCountAnomalyPlugin()
        plugin.config["device_anomaly_threshold"] = 3.0
        plugin.config["min_plays_for_anomaly"] = 5

        now = int(time.time())

        history = []
        for i in range(25):
            history.append({
                "play_time": now - i * 3600,
                "play_count_delta": 1,
                "device_id": "device_a",
                "source": "test",
            })
        for i in range(25):
            history.append({
                "play_time": now - i * 3600,
                "play_count_delta": 1,
                "device_id": "device_b",
                "source": "test",
            })

        item = Item(title="balanced", play_count=50)
        stats = {"mean": 10.0, "stddev": 5.0}
        anomaly = plugin._check_device_anomaly(item, history, stats, float(now))

        assert anomaly is None

    def test_get_daily_play_counts(self):
        plugin = PlayCountAnomalyPlugin()

        now = int(time.time())
        day1 = now - 86400
        day2 = now - 2 * 86400

        history = [
            {
                "play_time": now,
                "play_count_delta": 2,
                "device_id": "d1",
                "source": "s1",
            },
            {
                "play_time": now,
                "play_count_delta": 3,
                "device_id": "d2",
                "source": "s1",
            },
            {
                "play_time": day1,
                "play_count_delta": 5,
                "device_id": "d1",
                "source": "s2",
            },
            {
                "play_time": day2,
                "play_count_delta": 1,
                "device_id": "d1",
                "source": "s1",
            },
        ]

        daily = plugin._get_daily_play_counts(history)
        today_str = time.strftime("%Y-%m-%d", time.localtime(now))
        day1_str = time.strftime("%Y-%m-%d", time.localtime(day1))

        assert today_str in daily
        assert daily[today_str]["count"] == 5
        assert len(daily[today_str]["devices"]) == 2
        assert day1_str in daily
        assert daily[day1_str]["count"] == 5
        assert len(daily) == 3

    def test_item_types_include_new_fields(self):
        plugin = PlayCountAnomalyPlugin()

        assert "anomaly_score" in plugin.item_types
        assert "anomaly_types" in plugin.item_types
        assert "anomaly_reviewed" in plugin.item_types
        assert "anomaly_resolution" in plugin.item_types
        assert "anomaly_notes" in plugin.item_types
        assert "fake_play_count" in plugin.item_types
        assert "play_history" in plugin.item_types
        assert "daily_play_stats" in plugin.item_types

    def test_anomaly_types_constant(self):
        from beetsplug.playcountanomaly import ANOMALY_TYPES

        assert "zscore_deviation" in ANOMALY_TYPES
        assert "new_song_burst" in ANOMALY_TYPES
        assert "impossible_count" in ANOMALY_TYPES
        assert "sudden_zero" in ANOMALY_TYPES
        assert "daily_spike" in ANOMALY_TYPES
        assert "concurrent_play" in ANOMALY_TYPES
        assert "device_anomaly" in ANOMALY_TYPES
        assert len(ANOMALY_TYPES) == 7
