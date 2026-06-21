import re

import pytest

from beets.autotag.distance import (
    Distance,
    distance,
    string_dist,
    track_distance,
)
from beets.autotag.hooks import AlbumInfo, TrackInfo
from beets.library import Item
from beets.metadata_plugins import MetadataSourcePlugin, get_penalty
from beets.plugins import BeetsPlugin

_p = pytest.param


class TestDistance:
    @pytest.fixture(autouse=True, scope="class")
    def setup_config(self, config):
        config["match"]["distance_weights"]["data_source"] = 2.0
        config["match"]["distance_weights"]["album"] = 4.0
        config["match"]["distance_weights"]["medium"] = 2.0

    @pytest.fixture
    def dist(self):
        return Distance()

    def test_add(self, dist):
        dist.add("add", 1.0)

        assert dist._penalties == {"add": [1.0]}

    @pytest.mark.parametrize(
        "key, args_with_expected",
        [
            (
                "equality",
                [
                    (("ghi", ["abc", "def", "ghi"]), [0.0]),
                    (("xyz", ["abc", "def", "ghi"]), [0.0, 1.0]),
                    (("abc", re.compile(r"ABC", re.I)), [0.0, 1.0, 0.0]),
                ],
            ),
            ("expr", [((True,), [1.0]), ((False,), [1.0, 0.0])]),
            (
                "number",
                [
                    ((1, 1), [0.0]),
                    ((1, 2), [0.0, 1.0]),
                    ((2, 1), [0.0, 1.0, 1.0]),
                    ((-1, 2), [0.0, 1.0, 1.0, 1.0, 1.0, 1.0]),
                ],
            ),
            (
                "priority",
                [
                    (("abc", "abc"), [0.0]),
                    (("def", ["abc", "def"]), [0.0, 0.5]),
                    (("gh", ["ab", "cd", "ef", re.compile("GH", re.I)]), [0.0, 0.5, 0.75]),  # noqa: E501
                    (("xyz", ["abc", "def"]), [0.0, 0.5, 0.75, 1.0]),
                ],
            ),
            (
                "ratio",
                [
                    ((25, 100), [0.25]),
                    ((10, 5), [0.25, 1.0]),
                    ((-5, 5), [0.25, 1.0, 0.0]),
                    ((5, 0), [0.25, 1.0, 0.0, 0.0]),
                ],
            ),
            (
                "string",
                [
                    (("abc", "bcd"), [2 / 3]),
                    (("abc", None), [2 / 3, 1]),
                    ((None, None), [2 / 3, 1, 0]),
                ],
            ),
        ],
    )  # fmt: skip
    def test_add_methods(self, dist, key, args_with_expected):
        method = getattr(dist, f"add_{key}")
        for arg_set, expected in args_with_expected:
            method(key, *arg_set)
            assert dist._penalties[key] == expected

    def test_distance(self, dist):
        dist.add("album", 0.5)
        dist.add("media", 0.25)
        dist.add("media", 0.75)

        assert dist.distance == 0.5
        assert dist.max_distance == 6.0
        assert dist.raw_distance == 3.0

        assert dist["album"] == 1 / 3
        assert dist["media"] == 1 / 6

    def test_operators(self, dist):
        dist.add("data_source", 0.0)
        dist.add("album", 0.5)
        dist.add("medium", 0.25)
        dist.add("medium", 0.75)
        assert len(dist) == 2
        assert list(dist) == [("album", 0.2), ("medium", 0.2)]
        assert dist == 0.4
        assert dist < 1.0
        assert dist > 0.0
        assert dist - 0.4 == 0.0
        assert 0.4 - dist == 0.0
        assert float(dist) == 0.4

    def test_penalties_sort(self, dist):
        dist.add("album", 0.1875)
        dist.add("medium", 0.75)
        assert dist.items() == [("medium", 0.25), ("album", 0.125)]

        # Sort by key if distance is equal.
        dist = Distance()
        dist.add("album", 0.375)
        dist.add("medium", 0.75)
        assert dist.items() == [("album", 0.25), ("medium", 0.25)]

    def test_generic_penalty_keys_excludes_zero_penalties(self, dist):
        dist.add("album", 0.5)
        dist.add("label", 0.0)
        dist.add("medium", 0.25)
        assert dist.generic_penalty_keys == ["album", "medium"]

    def test_update(self, dist):
        dist1 = dist
        dist1.add("album", 0.5)
        dist1.add("media", 1.0)

        dist2 = Distance()
        dist2.add("album", 0.75)
        dist2.add("album", 0.25)
        dist2.add("media", 0.05)

        dist1.update(dist2)

        assert dist1._penalties == {
            "album": [0.5, 0.75, 0.25],
            "media": [1.0, 0.05],
        }


class TestTrackDistance:
    @pytest.fixture(scope="class")
    def info(self):
        return TrackInfo(title="title", artist="artist")

    @pytest.mark.parametrize(
        "title, artist, expected_penalty",
        [
            _p("title", "artist", False, id="identical"),
            _p("title", "Various Artists", False, id="tolerate-va"),
            _p("title", "different artist", True, id="different-artist"),
            _p("different title", "artist", True, id="different-title"),
        ],
    )
    def test_track_distance(self, info, title, artist, expected_penalty):
        item = Item(artist=artist, title=title)

        dist = track_distance(item, info, incl_artist=True)
        assert bool(dist) == expected_penalty, dist._penalties


class TestAlbumDistance:
    @pytest.fixture(scope="class")
    def items(self):
        return [
            Item(
                title=title,
                track=track,
                artist="artist",
                album="album",
                length=1,
            )
            for title, track in [("one", 1), ("two", 2), ("three", 3)]
        ]

    @pytest.fixture
    def get_dist(self, items):
        def inner(info: AlbumInfo):
            return distance(items, info, list(zip(items, info.tracks)))

        return inner

    @pytest.fixture
    def info(self, items):
        return AlbumInfo(
            artist="artist",
            album="album",
            tracks=[
                TrackInfo(
                    title=i.title,
                    artist=i.artist,
                    index=i.track,
                    length=i.length,
                )
                for i in items
            ],
            va=False,
        )

    def test_identical_albums(self, get_dist, info):
        assert get_dist(info) == 0

    def test_incomplete_album(self, get_dist, info):
        info.tracks.pop(2)

        assert 0 < float(get_dist(info)) < 0.2

    def test_overly_complete_album(self, get_dist, info):
        info.tracks.append(
            Item(index=4, title="four", artist="artist", length=1)
        )

        assert 0 < float(get_dist(info)) < 0.2

    @pytest.mark.parametrize("va", [True, False])
    def test_albumartist(self, get_dist, info, va):
        info.artist = "another artist"
        info.va = va

        assert bool(get_dist(info)) is not va

    def test_comp_no_track_artists(self, get_dist, info):
        # Some VA releases don't have track artists (incomplete metadata).
        info.artist = "another artist"
        info.va = True
        for track in info.tracks:
            track.artist = None

        assert get_dist(info) == 0

    def test_comp_track_artists_do_not_match(self, get_dist, info):
        info.va = True
        info.tracks[0].artist = "another artist"

        assert get_dist(info) != 0

    def test_tracks_out_of_order(self, get_dist, info):
        tracks = info.tracks
        tracks[1].title, tracks[2].title = tracks[2].title, tracks[1].title

        assert 0 < float(get_dist(info)) < 0.2

    def test_two_medium_release(self, get_dist, info):
        info.tracks[0].medium_index = 1
        info.tracks[1].medium_index = 2
        info.tracks[2].medium_index = 1

        assert get_dist(info) == 0


class TestStringDistance:
    @pytest.mark.parametrize(
        "string1, string2",
        [
            ("Some String", "Some String"),
            ("Some String", "Some.String!"),
            ("Some String", "sOME sTring"),
            ("My Song (EP)", "My Song"),
            ("The Song Title", "Song Title, The"),
            ("A Song Title", "Song Title, A"),
            ("An Album Title", "Album Title, An"),
            ("", ""),
            ("Untitled", "[Untitled]"),
            ("And", "&"),
            ("\xe9\xe1\xf1", "ean"),
        ],
    )
    def test_matching_distance(self, string1, string2):
        assert string_dist(string1, string2) == 0.0

    def test_different_distance(self):
        assert string_dist("Some String", "Totally Different") != 0.0

    @pytest.mark.parametrize(
        "string1, string2, reference",
        [
            ("XXX Band Name", "The Band Name", "Band Name"),
            ("One .Two.", "One (Two)", "One"),
            ("One .Two.", "One [Two]", "One"),
            ("My Song blah Someone", "My Song feat Someone", "My Song"),
        ],
    )
    def test_relative_weights(self, string1, string2, reference):
        assert string_dist(string2, reference) < string_dist(string1, reference)

    def test_solo_pattern(self):
        # Just make sure these don't crash.
        string_dist("The ", "")
        string_dist("(EP)", "(EP)")
        string_dist(", An", "")


class TestDataSourceDistance:
    MATCH = 0.0
    MISMATCH = 0.125

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch, penalty, weight, multiple_data_sources):
        monkeypatch.setitem(Distance._weights, "data_source", weight)
        get_penalty.cache_clear()

        class TestMetadataSourcePlugin(MetadataSourcePlugin):
            def album_for_id(self, *args, **kwargs): ...
            def track_for_id(self, *args, **kwargs): ...
            def candidates(self, *args, **kwargs): ...
            def item_candidates(self, *args, **kwargs): ...

        # We use BeetsPlugin here to check if our compatibility layer
        # for pre 2.4.0 MetadataPlugins is working as expected
        # TODO: Replace BeetsPlugin with TestMetadataSourcePlugin in v3.0.0
        with pytest.deprecated_call():

            class OriginalPlugin(BeetsPlugin):
                data_source = "Original"

        class OtherPlugin(TestMetadataSourcePlugin):
            @property
            def data_source_mismatch_penalty(self):
                return penalty

        monkeypatch.setattr(
            "beets.metadata_plugins.find_metadata_source_plugins",
            lambda: (
                [OriginalPlugin(), OtherPlugin()]
                if multiple_data_sources
                else [OtherPlugin()]
            ),
        )

    @pytest.mark.parametrize(
        "item,info,penalty,weight,multiple_data_sources,expected_distance",
        [
            _p("Original", "Original", 0.5, 1.0, True, MATCH, id="match"),
            _p("Original", "Other", 0.5, 1.0, True, MISMATCH, id="mismatch"),
            _p("Other", "Original", 0.5, 1.0, True, MISMATCH, id="mismatch"),
            _p("Original", "unknown", 0.5, 1.0, True, MISMATCH, id="mismatch-unknown"),
            _p("Original", None, 0.5, 1.0, True, MISMATCH, id="mismatch-no-info"),
            _p(None, "Other", 0.5, 1.0, True, MISMATCH, id="mismatch-no-original-multiple-sources"),  # noqa: E501
            _p(None, "Other", 0.5, 1.0, False, MATCH, id="match-no-original-but-single-source"),  # noqa: E501
            _p("unknown", "unknown", 0.5, 1.0, True, MATCH, id="match-unknown"),
            _p("Original", "Other", 1.0, 1.0, True, 0.25, id="mismatch-max-penalty"),
            _p("Original", "Other", 0.5, 5.0, True, 0.3125, id="mismatch-high-weight"),
            _p("Original", "Other", 0.0, 1.0, True, MATCH, id="match-no-penalty"),
            _p("Original", "Other", 0.5, 0.0, True, MATCH, id="match-no-weight"),
        ],
    )  # fmt: skip
    def test_distance(self, item, info, expected_distance):
        item = Item(data_source=item)
        info = TrackInfo(data_source=info, title="")

        dist = track_distance(item, info)

        assert dist.distance == expected_distance


class TestTrackDistanceSameNameLengthDifferentBitrate:
    """Edge case: tracks with the same title and length but different
    sample rates / bitrates should still produce a non-zero distance
    due to other distinguishing properties (e.g. bitrate), but the
    track_length penalty should be zero when lengths match.
    """

    @pytest.fixture(autouse=True)
    def config(self, config):
        config["match"]["track_length_grace"] = 10
        config["match"]["track_length_max"] = 30
        config["match"]["distance_weights"]["track_length"] = 2.0
        config["match"]["distance_weights"]["track_title"] = 3.0

    def test_same_length_zero_track_length_penalty(self):
        item = Item(
            title="Same Song",
            artist="Artist",
            length=200.0,
            bitrate=320000,
        )
        info = TrackInfo(
            title="Same Song",
            artist="Artist",
            length=200.0,
        )

        dist = track_distance(item, info, incl_artist=True)
        assert dist["track_length"] == 0.0

    def test_same_length_different_bitrate_no_length_penalty(self):
        item = Item(
            title="Same Song",
            artist="Artist",
            length=200.0,
            bitrate=128000,
        )
        info = TrackInfo(
            title="Same Song",
            artist="Artist",
            length=200.0,
        )

        dist = track_distance(item, info, incl_artist=True)
        assert dist["track_length"] == 0.0

    def test_same_length_different_title_has_penalty(self):
        item = Item(
            title="Song A",
            artist="Artist",
            length=200.0,
            bitrate=128000,
        )
        info = TrackInfo(
            title="Song B",
            artist="Artist",
            length=200.0,
        )

        dist = track_distance(item, info, incl_artist=True)
        assert dist["track_length"] == 0.0
        assert dist["track_title"] > 0.0

    def test_length_within_grace_no_penalty(self):
        item = Item(
            title="Song",
            artist="Artist",
            length=200.0,
        )
        info = TrackInfo(
            title="Song",
            artist="Artist",
            length=205.0,
        )

        dist = track_distance(item, info, incl_artist=True)
        assert dist["track_length"] == 0.0

    def test_length_beyond_grace_has_penalty(self):
        item = Item(
            title="Song",
            artist="Artist",
            length=200.0,
        )
        info = TrackInfo(
            title="Song",
            artist="Artist",
            length=250.0,
        )

        dist = track_distance(item, info, incl_artist=True)
        assert dist["track_length"] > 0.0

    def test_same_name_length_different_artist_has_penalty(self):
        item = Item(
            title="Same Song",
            artist="Artist A",
            length=200.0,
        )
        info = TrackInfo(
            title="Same Song",
            artist="Artist B",
            length=200.0,
        )

        dist = track_distance(item, info, incl_artist=True)
        assert dist["track_length"] == 0.0
        assert dist["track_artist"] > 0.0

    def test_zero_length_no_penalty(self):
        item = Item(
            title="Song",
            artist="Artist",
            length=0,
        )
        info = TrackInfo(
            title="Song",
            artist="Artist",
            length=0,
        )

        dist = track_distance(item, info, incl_artist=True)
        assert "track_length" not in dist.keys()

    def test_item_no_length_info_no_penalty(self):
        item = Item(
            title="Song",
            artist="Artist",
        )
        info = TrackInfo(
            title="Song",
            artist="Artist",
            length=None,
        )

        dist = track_distance(item, info, incl_artist=True)
        assert "track_length" not in dist.keys()


class TestTrackDistanceSameNameLengthDifferentSampleRate:
    """Edge case: tracks with same title and length but different sample rates.

    These tests verify that:
    - Sample rate differences don't cause false mismatches when other
      metadata matches
    - The matching logic correctly identifies tracks even with different
      audio quality
    - Sample rate is not used as a distinguishing factor in the distance
      calculation (as it's not part of the distance formula)
    """

    @pytest.fixture(autouse=True)
    def config(self, config):
        config["match"]["track_length_grace"] = 10
        config["match"]["track_length_max"] = 30
        config["match"]["distance_weights"]["track_length"] = 2.0
        config["match"]["distance_weights"]["track_title"] = 3.0
        config["match"]["distance_weights"]["track_artist"] = 2.0

    def test_same_name_length_different_samplerate_44100_vs_48000(self):
        """Test tracks with same name/length, different sample rates (44.1 vs 48 kHz).

        This is a common scenario when comparing CD-quality (44.1 kHz)
        and professional/studio quality (48 kHz) versions of the same track.
        """
        item = Item(
            title="Same Song",
            artist="Same Artist",
            length=200.0,
            samplerate=44100,
            bitrate=320000,
        )
        info = TrackInfo(
            title="Same Song",
            artist="Same Artist",
            length=200.0,
        )

        dist = track_distance(item, info, incl_artist=True)
        assert dist["track_length"] == 0.0
        assert dist["track_title"] == 0.0
        assert dist["track_artist"] == 0.0
        assert float(dist) == 0.0

    def test_same_name_length_different_samplerate_96000(self):
        """Test high-res audio (96 kHz) vs standard quality.

        Verify that high-resolution versions of the same track
        are correctly matched.
        """
        item = Item(
            title="Same Song",
            artist="Same Artist",
            length=200.0,
            samplerate=96000,
            bitrate=1411200,
        )
        info = TrackInfo(
            title="Same Song",
            artist="Same Artist",
            length=200.0,
        )

        dist = track_distance(item, info, incl_artist=True)
        assert float(dist) == 0.0

    def test_same_name_length_different_samplerate_192000(self):
        """Test ultra high-res (192 kHz) vs standard quality."""
        item = Item(
            title="Same Song",
            artist="Same Artist",
            length=200.0,
            samplerate=192000,
            bitrate=2822400,
        )
        info = TrackInfo(
            title="Same Song",
            artist="Same Artist",
            length=200.0,
        )

        dist = track_distance(item, info, incl_artist=True)
        assert float(dist) == 0.0

    def test_same_name_length_different_bitrate(self):
        """Test tracks with different bitrates but same metadata.

        MP3 128kbps vs 320kbps versions of the same track
        should match perfectly.
        """
        item_low_quality = Item(
            title="Same Song",
            artist="Same Artist",
            length=200.0,
            samplerate=44100,
            bitrate=128000,
        )
        item_high_quality = Item(
            title="Same Song",
            artist="Same Artist",
            length=200.0,
            samplerate=44100,
            bitrate=320000,
        )
        info = TrackInfo(
            title="Same Song",
            artist="Same Artist",
            length=200.0,
        )

        dist_low = track_distance(item_low_quality, info, incl_artist=True)
        dist_high = track_distance(item_high_quality, info, incl_artist=True)

        assert float(dist_low) == float(dist_high) == 0.0

    def test_same_name_length_different_samplerate_with_other_differences(self):
        """Test that sample rate differences don't mask actual metadata differences.

        When tracks have different titles/artists, they should still
        produce a non-zero distance regardless of sample rate.
        """
        item = Item(
            title="Song A",
            artist="Artist A",
            length=200.0,
            samplerate=44100,
        )
        info = TrackInfo(
            title="Song B",
            artist="Artist B",
            length=200.0,
        )

        dist = track_distance(item, info, incl_artist=True)
        assert dist["track_length"] == 0.0
        assert dist["track_title"] > 0.0
        assert dist["track_artist"] > 0.0
        assert float(dist) > 0.0

    def test_multiple_tracks_same_name_length_different_samplerate(self):
        """Test assigning multiple tracks with varying sample rates.

        When assigning tracks in an album, sample rate differences
        should not interfere with correct track assignment.
        """
        items = [
            Item(
                title=f"Track {i}",
                artist="Artist",
                length=180.0 + i * 10,
                samplerate=samplerate,
            )
            for i, samplerate in enumerate([44100, 48000, 96000])
        ]
        tracks = [
            TrackInfo(
                title=f"Track {i}",
                artist="Artist",
                length=180.0 + i * 10,
                index=i + 1,
            )
            for i in range(3)
        ]

        from beets.autotag.match import assign_items

        mapping, extra_items, extra_tracks = assign_items(items, tracks)

        assert len(mapping) == 3
        assert len(extra_items) == 0
        assert len(extra_tracks) == 0
        for item, track in mapping:
            assert item.title == track.title

    def test_same_name_length_different_samplerate_album_distance(self):
        """Test album-level distance calculation with mixed sample rates.

        When matching an album where individual tracks have different
        sample rates, the overall album distance should still be zero
        for a perfect metadata match.
        """
        from beets.autotag.distance import distance
        from beets.autotag.hooks import AlbumInfo

        items = [
            Item(
                title=f"Track {i}",
                artist="Artist",
                album="Album",
                length=200.0,
                samplerate=samplerate,
                track=i + 1,
            )
            for i, samplerate in enumerate([44100, 48000, 96000])
        ]
        tracks = [
            TrackInfo(
                title=f"Track {i}",
                artist="Artist",
                length=200.0,
                index=i + 1,
            )
            for i in range(3)
        ]
        info = AlbumInfo(
            tracks,
            artist="Artist",
            album="Album",
            va=False,
        )

        item_info_pairs = list(zip(items, tracks))
        dist = distance(items, info, item_info_pairs)

        assert float(dist) == 0.0
        for track_dist in dist.tracks.values():
            assert float(track_dist) == 0.0

    def test_samplerate_not_in_distance_keys(self):
        """Verify that samplerate is not used as a distance penalty key.

        The distance calculation should not include sample rate as a
        penalty factor, since it's an audio quality attribute rather
        than a metadata attribute.
        """
        item = Item(
            title="Song",
            artist="Artist",
            length=200.0,
            samplerate=44100,
        )
        info = TrackInfo(
            title="Song",
            artist="Artist",
            length=200.0,
        )

        dist = track_distance(item, info, incl_artist=True)
        assert "samplerate" not in dist.keys()
        assert "sample_rate" not in dist.keys()

    def test_same_name_length_88200_vs_96000(self):
        """Test 88.2 kHz vs 96 kHz sample rate differences."""
        item_88 = Item(
            title="High-Res Song",
            artist="High-Res Artist",
            length=300.0,
            samplerate=88200,
            bitrate=1411200,
        )
        item_96 = Item(
            title="High-Res Song",
            artist="High-Res Artist",
            length=300.0,
            samplerate=96000,
            bitrate=1536000,
        )
        info = TrackInfo(
            title="High-Res Song",
            artist="High-Res Artist",
            length=300.0,
        )

        dist_88 = track_distance(item_88, info, incl_artist=True)
        dist_96 = track_distance(item_96, info, incl_artist=True)

        assert float(dist_88) == 0.0
        assert float(dist_96) == 0.0
        assert float(dist_88) == float(dist_96)

    def test_same_name_length_different_samplerate_with_grace_period(self):
        """Test that length grace period works with different sample rates.

        Even with slightly different lengths (within grace period),
        sample rate differences should not affect the match.
        """
        item = Item(
            title="Song",
            artist="Artist",
            length=200.0,
            samplerate=44100,
        )
        info = TrackInfo(
            title="Song",
            artist="Artist",
            length=205.0,
        )

        dist = track_distance(item, info, incl_artist=True)
        assert dist["track_length"] == 0.0
        assert float(dist) == 0.0

    def test_lossless_vs_lossy_same_name_length(self):
        """Test lossless (FLAC) vs lossy (MP3) versions of the same track.

        Lossless and lossy versions often have the same metadata but
        different audio quality characteristics. They should match
        perfectly.
        """
        item_lossless = Item(
            title="Song",
            artist="Artist",
            length=200.0,
            samplerate=44100,
            bitrate=1411200,
            format="FLAC",
        )
        item_lossy = Item(
            title="Song",
            artist="Artist",
            length=200.0,
            samplerate=44100,
            bitrate=320000,
            format="MP3",
        )
        info = TrackInfo(
            title="Song",
            artist="Artist",
            length=200.0,
        )

        dist_lossless = track_distance(item_lossless, info, incl_artist=True)
        dist_lossy = track_distance(item_lossy, info, incl_artist=True)

        assert float(dist_lossless) == 0.0
        assert float(dist_lossy) == 0.0
        assert float(dist_lossless) == float(dist_lossy)

    def test_same_name_length_different_samplerate_va_album(self):
        """Test sample rate differences in Various Artists albums.

        In VA albums, track artist comparison is enabled. Sample rate
        differences should not interfere with correct matching even
        when track artists vary.
        """
        item = Item(
            title="Compilation Track",
            artist="Various Artists",
            length=250.0,
            samplerate=48000,
            comp=True,
        )
        info = TrackInfo(
            title="Compilation Track",
            artist="Specific Artist",
            length=250.0,
        )

        dist = track_distance(item, info, incl_artist=True)
        assert dist["track_length"] == 0.0
        assert dist["track_title"] == 0.0
