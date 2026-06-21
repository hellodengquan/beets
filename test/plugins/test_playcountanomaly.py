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
