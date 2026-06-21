import time
from beets.library import Item
from beetsplug.playcountanomaly import PlayCountAnomalyPlugin, _median, _stddev

def test_helper_functions():
    print("Testing helper functions...")
    assert _median([]) == 0.0
    assert _median([1.0, 3.0, 2.0]) == 2.0
    assert _median([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert _stddev([5.0], 5.0) == 0.0
    
    values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    mean = sum(values) / len(values)
    std = _stddev(values, mean)
    assert abs(std - 2.0) < 0.01, f"Expected ~2.0, got {std}"
    print("  Helper functions: OK")

def test_plugin_instantiation():
    print("Testing plugin instantiation...")
    plugin = PlayCountAnomalyPlugin()
    assert plugin is not None
    print("  Plugin instantiation: OK")
    return plugin

def test_zscore_deviation(plugin):
    print("Testing z-score deviation detection...")
    stats = {"mean": 10.0, "stddev": 5.0}
    item = Item(title="test", play_count=25)
    now = time.time()
    anomalies = plugin.detect_anomalies(item, stats, now=now)
    types = [a.type for a in anomalies]
    assert "zscore_deviation" in types, f"Expected zscore_deviation, got {types}"
    print("  Z-score deviation detection: OK")

def test_no_zscore_deviation(plugin):
    print("Testing no z-score deviation for normal values...")
    stats = {"mean": 10.0, "stddev": 5.0}
    item = Item(title="normal", play_count=12)
    now = time.time()
    anomalies = plugin.detect_anomalies(item, stats, now=now)
    types = [a.type for a in anomalies]
    assert "zscore_deviation" not in types, f"Unexpected zscore_deviation: {types}"
    print("  No z-score deviation for normal values: OK")

def test_impossible_count(plugin):
    print("Testing impossible count detection...")
    now = time.time()
    added = now - 100
    item = Item(title="impossible", play_count=10, length=30.0, added=added)
    anomalies = plugin.detect_anomalies(item, {"mean": 0, "stddev": 0}, now=now)
    types = [a.type for a in anomalies]
    assert "impossible_count" in types, f"Expected impossible_count, got {types}"
    print("  Impossible count detection: OK")

def test_sudden_zero(plugin):
    print("Testing sudden zero detection...")
    now = time.time()
    last_played = now - 86400
    item = Item(title="zero", play_count=0, last_played=last_played)
    anomalies = plugin.detect_anomalies(item, {"mean": 0, "stddev": 0}, now=now)
    types = [a.type for a in anomalies]
    assert "sudden_zero" in types, f"Expected sudden_zero, got {types}"
    print("  Sudden zero detection: OK")

def test_no_sudden_zero_old(plugin):
    print("Testing no sudden zero for old plays...")
    now = time.time()
    last_played = now - 60 * 86400
    item = Item(title="zero_old", play_count=0, last_played=last_played)
    anomalies = plugin.detect_anomalies(item, {"mean": 0, "stddev": 0}, now=now)
    types = [a.type for a in anomalies]
    assert "sudden_zero" not in types, f"Unexpected sudden_zero: {types}"
    print("  No sudden zero for old plays: OK")

def test_new_song_burst(plugin):
    print("Testing new song burst detection...")
    now = time.time()
    added = now - 86400
    item = Item(title="burst", play_count=20, added=added, length=200.0)
    stats = {"mean": 0, "stddev": 0, "median_per_day": 1.0}
    anomalies = plugin.detect_anomalies(item, stats, now=now)
    types = [a.type for a in anomalies]
    assert "new_song_burst" in types, f"Expected new_song_burst, got {types}"
    print("  New song burst detection: OK")

def test_old_song_no_burst(plugin):
    print("Testing no new song burst for old songs...")
    now = time.time()
    added = now - 30 * 86400
    item = Item(title="old", play_count=100, added=added, length=200.0)
    stats = {"mean": 0, "stddev": 0, "median_per_day": 1.0}
    anomalies = plugin.detect_anomalies(item, stats, now=now)
    types = [a.type for a in anomalies]
    assert "new_song_burst" not in types, f"Unexpected new_song_burst: {types}"
    print("  No new song burst for old songs: OK")

def test_item_types(plugin):
    print("Testing item types...")
    assert "anomaly_score" in plugin.item_types
    assert "anomaly_types" in plugin.item_types
    assert "anomaly_reviewed" in plugin.item_types
    assert "anomaly_resolution" in plugin.item_types
    assert "anomaly_notes" in plugin.item_types
    assert "fake_play_count" in plugin.item_types
    print("  Item types: OK")

def test_commands(plugin):
    print("Testing commands...")
    cmds = plugin.commands()
    assert len(cmds) == 1
    cmd = cmds[0]
    assert cmd.name == "anomalies"
    print("  Commands: OK")

def main():
    print("=" * 60)
    print("PlayCountAnomalyPlugin - Core Logic Tests")
    print("=" * 60)
    print()
    
    test_helper_functions()
    print()
    
    plugin = test_plugin_instantiation()
    print()
    
    test_zscore_deviation(plugin)
    test_no_zscore_deviation(plugin)
    print()
    
    test_impossible_count(plugin)
    print()
    
    test_sudden_zero(plugin)
    test_no_sudden_zero_old(plugin)
    print()
    
    test_new_song_burst(plugin)
    test_old_song_no_burst(plugin)
    print()
    
    test_item_types(plugin)
    test_commands(plugin)
    print()
    
    print("=" * 60)
    print("ALL TESTS PASSED!")
    print("=" * 60)

if __name__ == "__main__":
    main()
