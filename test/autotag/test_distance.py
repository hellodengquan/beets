import re

import pytest

from beets.autotag.distance import (
    AlbumScoringContext,
    AlbumScoringPipeline,
    Distance,
    ScoringStage,
    TrackScoringContext,
    TrackScoringPipeline,
    distance,
    string_dist,
    track_distance,
    _safe_plugin_send,
)
from beets.autotag.hooks import AlbumInfo, TrackInfo
from beets.library import Item
from beets.metadata_plugins import MetadataSourcePlugin, get_penalty
from beets.plugins import BeetsPlugin, send

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


class TestScoringPipelineExceptionSafety:
    """Tests that ensure plugin hook exceptions do not abort the pipeline."""

    @pytest.fixture(autouse=True)
    def _setup_config(self, config):
        config["match"]["track_length_grace"] = 10
        config["match"]["track_length_max"] = 30
        BeetsPlugin.listeners.clear()

    def _register_listener(self, event, listener):
        BeetsPlugin.listeners[event].append(listener)

    @pytest.fixture
    def item(self):
        return Item(
            title="title",
            artist="artist",
            length=200,
            track=1,
            disc=1,
        )

    @pytest.fixture
    def track_info(self):
        return TrackInfo(
            title="title",
            artist="artist",
            length=200,
            index=1,
            medium=1,
        )

    @pytest.fixture
    def items(self):
        return [
            Item(
                title=f"track_{i}",
                artist="the artist",
                album="the album",
                length=200 + i,
                track=i + 1,
                disc=1,
            )
            for i in range(3)
        ]

    @pytest.fixture
    def album_info(self, items):
        return AlbumInfo(
            artist="the artist",
            album="the album",
            tracks=[
                TrackInfo(
                    title=i.title,
                    artist=i.artist,
                    index=i.track,
                    medium=i.disc,
                    length=i.length,
                )
                for i in items
            ],
            va=False,
        )

    @pytest.fixture
    def pairs(self, items, album_info):
        return list(zip(items, album_info.tracks))

    # ------------------------------------------------------------------ #
    # plugins.send() listener isolation
    # ------------------------------------------------------------------ #
    def test_send_isolates_listener_valueerror(self):
        """A ValueError from one listener does not affect others."""

        def bad_listener(**_):
            raise ValueError("boom from bad listener")

        ok_results = []

        def good_listener(**_):
            ok_results.append("I ran")
            return "return-value"

        self._register_listener("album_candidate_scored", bad_listener)
        self._register_listener("album_candidate_scored", good_listener)

        results = send("album_candidate_scored", context=None)

        assert results == ["return-value"]
        assert ok_results == ["I ran"]

    def test_send_isolates_listener_runtimeerror(self):
        """A RuntimeError from one listener does not affect others."""

        def bad_listener(**_):
            raise RuntimeError("runtime boom")

        ok_results = []

        def good_listener(**_):
            ok_results.append("I ran too")

        self._register_listener("track_candidate_scored", bad_listener)
        self._register_listener("track_candidate_scored", good_listener)

        send("track_candidate_scored", context=None)

        assert ok_results == ["I ran too"]

    # ------------------------------------------------------------------ #
    # _safe_plugin_send() top-level safety
    # ------------------------------------------------------------------ #
    def test_safe_plugin_send_returns_empty_on_unexpected_error(self, monkeypatch):
        """_safe_plugin_send catches even errors raised before dispatch."""

        def explode(**_):
            raise TypeError("pre-dispatch error")

        monkeypatch.setattr(
            "beets.autotag.distance.plugins.send", explode
        )

        assert _safe_plugin_send("any_event") == []

    # ------------------------------------------------------------------ #
    # TrackScoringPipeline: hook exceptions
    # ------------------------------------------------------------------ #
    def test_track_pipeline_survives_valueerror_in_plugin_custom_hook(
        self, item, track_info
    ):
        """ValueError in track_candidate_scored hook does not abort scoring."""

        def raise_valueerror(context):
            raise ValueError("custom plugin crashed")

        self._register_listener("track_candidate_scored", raise_valueerror)

        dist = TrackScoringPipeline(item, track_info, incl_artist=True).score()

        assert dist is not None
        assert isinstance(dist, Distance)

    def test_track_pipeline_survives_runtimeerror_in_plugin_custom_hook(
        self, item, track_info
    ):
        """RuntimeError in track_candidate_scored hook does not abort scoring."""

        def raise_runtime(context):
            raise RuntimeError("runtime plugin crashed")

        self._register_listener("track_candidate_scored", raise_runtime)

        dist = TrackScoringPipeline(item, track_info, incl_artist=True).score()

        assert dist is not None
        assert isinstance(dist, Distance)

    def test_track_pipeline_survives_exception_in_initialize_hook(
        self, item, track_info
    ):
        """Exception during INITIALIZE stage is swallowed."""

        def raise_it(context):
            raise ValueError("init hook broken")

        self._register_listener("track_distance_calculated", raise_it)

        dist = TrackScoringPipeline(item, track_info, incl_artist=False).score()

        assert isinstance(dist, Distance)

    def test_track_pipeline_completes_score_even_when_finalize_hook_raises(
        self, item, track_info
    ):
        """FINALIZE hook exception is logged but score is still returned."""

        def raise_finalize(context):
            if context.stage == ScoringStage.FINALIZE:
                raise RuntimeError("finalize failed")

        self._register_listener("track_distance_calculated", raise_finalize)

        dist = TrackScoringPipeline(item, track_info, incl_artist=False).score()

        assert isinstance(dist, Distance)

    # ------------------------------------------------------------------ #
    # AlbumScoringPipeline: hook exceptions
    # ------------------------------------------------------------------ #
    def test_album_pipeline_survives_valueerror_in_plugin_custom_hook(
        self, items, album_info, pairs
    ):
        """ValueError in album_candidate_scored hook does not abort scoring."""

        def raise_valueerror(context):
            raise ValueError("album plugin broken")

        self._register_listener("album_candidate_scored", raise_valueerror)

        dist = AlbumScoringPipeline(items, album_info, pairs).score()

        assert isinstance(dist, Distance)

    def test_album_pipeline_survives_runtimeerror_in_plugin_custom_hook(
        self, items, album_info, pairs
    ):
        """RuntimeError in album_candidate_scored hook does not abort scoring."""

        def raise_runtime(context):
            raise RuntimeError("album runtime failed")

        self._register_listener("album_candidate_scored", raise_runtime)

        dist = AlbumScoringPipeline(items, album_info, pairs).score()

        assert isinstance(dist, Distance)

    def test_album_pipeline_survives_multiple_bad_listeners(
        self, items, album_info, pairs
    ):
        """Multiple failing listeners on different events are all handled."""

        def raise_valueerror(context):
            raise ValueError("value error")

        def raise_runtime(context):
            raise RuntimeError("runtime error")

        self._register_listener("album_distance_calculated", raise_valueerror)
        self._register_listener("album_candidate_scored", raise_runtime)

        dist = AlbumScoringPipeline(items, album_info, pairs).score()

        assert isinstance(dist, Distance)

    def test_album_pipeline_stage_exception_is_isolated(
        self, items, album_info, pairs
    ):
        """A direct stage failure (not only hooks) is caught by the loop."""

        ctx = AlbumScoringContext(
            items=items, album_info=album_info, item_info_pairs=pairs,
            likelies={},
        )
        pipeline = AlbumScoringPipeline.__new__(AlbumScoringPipeline)
        pipeline.ctx = ctx

        def broken_stage():
            raise KeyError("stage missing data")

        pipeline._stage_album_metadata = broken_stage

        dist = pipeline.score()

        assert isinstance(dist, Distance)

    # ------------------------------------------------------------------ #
    # Distance results remain meaningful after hook exceptions
    # ------------------------------------------------------------------ #
    def test_track_score_still_computed_after_plugin_exception(
        self, item, track_info
    ):
        """Base penalties are recorded even when plugin custom hook raises."""

        def raise_valueerror(context):
            raise ValueError("plugin crashed")

        self._register_listener("track_candidate_scored", raise_valueerror)

        item.title = "different title"
        track_info.title = "totally different"

        dist = TrackScoringPipeline(item, track_info, incl_artist=False).score()

        penalties = dict(dist.items())
        assert "track_title" in penalties
        assert penalties["track_title"] > 0

    def test_album_score_still_computed_after_plugin_exception(
        self, items, album_info, pairs
    ):
        """Album penalties accumulate even when plugin custom hook raises."""

        def raise_runtime(context):
            raise RuntimeError("boom")

        self._register_listener("album_candidate_scored", raise_runtime)

        album_info.artist = "different artist"

        dist = AlbumScoringPipeline(items, album_info, pairs).score()

        penalties = dict(dist.items())
        assert "artist" in penalties
        assert penalties["artist"] > 0
