from typing import ClassVar

import pytest

from beets import metadata_plugins
from beets.autotag.hooks import AlbumInfo, TrackInfo
from beets.autotag.match import Recommendation, assign_items, tag_album, tag_item
from beets.library import Item


class TestAssignment:
    A = "one"
    B = "two"
    C = "three"

    @pytest.fixture(autouse=True)
    def config(self, config):
        config["match"]["track_length_grace"] = 10
        config["match"]["track_length_max"] = 30

    @pytest.mark.parametrize(
        # 'expected' is a tuple of expected (mapping, extra_items, extra_tracks)
        "item_titles, track_titles, expected",
        [
            # items ordering gets corrected
            ([A, C, B], [A, B, C], ({A: A, B: B, C: C}, [], [])),
            # unmatched tracks are returned as 'extra_tracks'
            # the first track is unmatched
            ([B, C], [A, B, C], ({B: B, C: C}, [], [A])),
            # the middle track is unmatched
            ([A, C], [A, B, C], ({A: A, C: C}, [], [B])),
            # the last track is unmatched
            ([A, B], [A, B, C], ({A: A, B: B}, [], [C])),
            # unmatched items are returned as 'extra_items'
            ([A, C, B], [A, C], ({A: A, C: C}, [B], [])),
        ],
    )
    def test_assign_tracks(self, item_titles, track_titles, expected):
        expected_mapping, expected_extra_items, expected_extra_tracks = expected

        items = [Item(title=title) for title in item_titles]
        tracks = [TrackInfo(title=title) for title in track_titles]

        item_info_pairs, extra_items, extra_tracks = assign_items(items, tracks)

        assert (
            {i.title: t.title for i, t in item_info_pairs},
            [i.title for i in extra_items],
            [t.title for t in extra_tracks],
        ) == (expected_mapping, expected_extra_items, expected_extra_tracks)

    def test_order_works_when_track_names_are_entirely_wrong(self):
        # A real-world test case contributed by a user.
        def item(i, length):
            return Item(
                artist="ben harper",
                album="burn to shine",
                title=f"ben harper - Burn to Shine {i}",
                track=i,
                length=length,
            )

        items = []
        items.append(item(1, 241.37243007106997))
        items.append(item(2, 342.27781704375036))
        items.append(item(3, 245.95070222338137))
        items.append(item(4, 472.87662515485437))
        items.append(item(5, 279.1759535763187))
        items.append(item(6, 270.33333768012))
        items.append(item(7, 247.83435613222923))
        items.append(item(8, 216.54504531525072))
        items.append(item(9, 225.72775379800484))
        items.append(item(10, 317.7643606963552))
        items.append(item(11, 243.57001238834192))
        items.append(item(12, 186.45916150485752))

        def info(index, title, length):
            return TrackInfo(title=title, length=length, index=index)

        trackinfo = []
        trackinfo.append(info(1, "Alone", 238.893))
        trackinfo.append(info(2, "The Woman in You", 341.44))
        trackinfo.append(info(3, "Less", 245.59999999999999))
        trackinfo.append(info(4, "Two Hands of a Prayer", 470.49299999999999))
        trackinfo.append(info(5, "Please Bleed", 277.86599999999999))
        trackinfo.append(info(6, "Suzie Blue", 269.30599999999998))
        trackinfo.append(info(7, "Steal My Kisses", 245.36000000000001))
        trackinfo.append(info(8, "Burn to Shine", 214.90600000000001))
        trackinfo.append(info(9, "Show Me a Little Shame", 224.0929999999999))
        trackinfo.append(info(10, "Forgiven", 317.19999999999999))
        trackinfo.append(info(11, "Beloved One", 243.733))
        trackinfo.append(info(12, "In the Lord's Arms", 186.13300000000001))

        expected = list(zip(items, trackinfo)), [], []

        assert assign_items(items, trackinfo) == expected


class TestTagMultipleDataSources:
    @pytest.fixture
    def shared_track_id(self):
        return "track-12345"

    @pytest.fixture
    def shared_album_id(self):
        return "album-12345"

    @pytest.fixture(autouse=True)
    def _setup_plugins(self, monkeypatch, shared_album_id, shared_track_id):
        class StubPlugin:
            data_source: ClassVar[str]
            data_source_mismatch_penalty = 0

            @property
            def track(self):
                return TrackInfo(
                    artist="Artist",
                    title="Title",
                    track_id=shared_track_id,
                    data_source=self.data_source,
                )

            @property
            def album(self):
                return AlbumInfo(
                    [self.track],
                    artist="Albumartist",
                    album="Album",
                    album_id=shared_album_id,
                    data_source=self.data_source,
                )

            def albums_for_ids(self, *_):
                yield self.album

            def tracks_for_ids(self, *_):
                yield self.track

            def candidates(self, *_, **__):
                yield self.album

            def item_candidates(self, *_, **__):
                yield self.track

        class DeezerPlugin(StubPlugin):
            data_source = "Deezer"

        class DiscogsPlugin(StubPlugin):
            data_source = "Discogs"

        monkeypatch.setattr(
            metadata_plugins,
            "find_metadata_source_plugins",
            lambda: [DeezerPlugin(), DiscogsPlugin()],
        )

    def check_proposal(self, proposal):
        sources = [
            candidate.info.data_source for candidate in proposal.candidates
        ]
        assert len(sources) == 2
        assert set(sources) == {"Discogs", "Deezer"}

    def test_search_album_ids(self, shared_album_id):
        _, _, proposal = tag_album([Item()], search_ids=[shared_album_id])

        self.check_proposal(proposal)

    def test_search_album_current_id(self, shared_album_id):
        _, _, proposal = tag_album([Item(mb_albumid=shared_album_id)])

        self.check_proposal(proposal)

    def test_search_track_ids(self, shared_track_id):
        proposal = tag_item(Item(), search_ids=[shared_track_id])

        self.check_proposal(proposal)

    def test_search_track_current_id(self, shared_track_id):
        proposal = tag_item(Item(mb_trackid=shared_track_id))

        self.check_proposal(proposal)


class TestRecommendationThresholds:
    """Test the _recommendation function with different threshold levels."""

    @pytest.fixture(autouse=True)
    def config(self, config):
        config["match"]["strong_rec_thresh"] = 0.04
        config["match"]["medium_rec_thresh"] = 0.25
        config["match"]["rec_gap_thresh"] = 0.25

    @pytest.fixture
    def make_album_match(self):
        from beets.autotag.distance import Distance
        from beets.autotag.hooks import AlbumMatch
        from beets.autotag.match import _recommendation

        def _make(distance_value):
            dist = Distance()
            dist.add("album", distance_value)
            match = AlbumMatch(dist, None, {}, [], [])
            return match

        return _make

    def test_no_candidates_yields_none(self):
        from beets.autotag.match import _recommendation

        assert _recommendation([]) == Recommendation.none

    def test_strong_recommendation(self, make_album_match):
        from beets.autotag.match import _recommendation

        results = [make_album_match(0.02)]
        rec = _recommendation(results)
        assert rec == Recommendation.strong

    def test_strong_threshold_boundary(self, make_album_match):
        from beets.autotag.match import _recommendation

        results = [make_album_match(0.04)]
        rec = _recommendation(results)
        assert rec == Recommendation.medium

    def test_just_below_strong_threshold(self, make_album_match):
        from beets.autotag.match import _recommendation

        results = [make_album_match(0.039)]
        rec = _recommendation(results)
        assert rec == Recommendation.strong

    def test_medium_recommendation(self, make_album_match):
        from beets.autotag.match import _recommendation

        results = [make_album_match(0.15)]
        rec = _recommendation(results)
        assert rec == Recommendation.medium

    def test_medium_threshold_boundary(self, make_album_match):
        from beets.autotag.match import _recommendation

        results = [make_album_match(0.25)]
        rec = _recommendation(results)
        assert rec == Recommendation.medium

    def test_low_recommendation_single_candidate(self, make_album_match):
        from beets.autotag.match import _recommendation

        results = [make_album_match(0.30)]
        rec = _recommendation(results)
        assert rec == Recommendation.low

    def test_low_recommendation_with_gap(self, make_album_match):
        from beets.autotag.match import _recommendation

        results = [make_album_match(0.30), make_album_match(0.60)]
        rec = _recommendation(results)
        assert rec == Recommendation.low

    def test_none_recommendation_no_gap(self, make_album_match):
        from beets.autotag.match import _recommendation

        results = [make_album_match(0.30), make_album_match(0.35)]
        rec = _recommendation(results)
        assert rec == Recommendation.none

    def test_gap_threshold_boundary(self, make_album_match):
        from beets.autotag.match import _recommendation

        results = [make_album_match(0.30), make_album_match(0.55)]
        rec = _recommendation(results)
        assert rec == Recommendation.low

    def test_gap_just_below_threshold(self, make_album_match):
        from beets.autotag.match import _recommendation

        results = [make_album_match(0.30), make_album_match(0.549)]
        rec = _recommendation(results)
        assert rec == Recommendation.none

    def test_max_rec_downgrade(self, make_album_match, monkeypatch):
        from beets.autotag.match import _recommendation
        from beets import config as beets_config

        original_max_rec = beets_config["match"]["max_rec"].get(dict)
        beets_config["match"]["max_rec"]["album"] = "low"
        try:
            results = [make_album_match(0.02)]
            rec = _recommendation(results)
            assert rec <= Recommendation.low
        finally:
            beets_config["match"]["max_rec"] = original_max_rec


class TestRecommendationThresholdsExtended:
    """Extended edge case tests for recommendation threshold grading.

    These tests cover:
    - Threshold boundary conditions with high precision
    - Multiple penalty keys affecting recommendation
    - max_rec downgrade for different penalty types
    - Track-level distance affecting overall recommendation
    - Gap threshold edge cases
    """

    @pytest.fixture(autouse=True)
    def config(self):
        from beets import config as beets_config
        from beets.test.helper import ConfigMixin

        original_sources = beets_config.sources
        beets_config.sources = []
        beets_config.read(user=False, defaults=True)

        beets_config["plugins"] = []
        beets_config["verbose"] = 1
        beets_config["ui"]["color"] = False
        beets_config["threaded"] = False

        beets_config["match"]["strong_rec_thresh"] = 0.04
        beets_config["match"]["medium_rec_thresh"] = 0.25
        beets_config["match"]["rec_gap_thresh"] = 0.25
        beets_config["match"]["distance_weights"]["album"] = 1.0
        beets_config["match"]["distance_weights"]["tracks"] = 1.0
        beets_config["match"]["distance_weights"]["artist"] = 1.0
        beets_config["match"]["track_length_grace"] = 10
        beets_config["match"]["track_length_max"] = 30

        yield beets_config

        beets_config.sources = original_sources
        beets_config.read(user=False, defaults=True)

    @pytest.fixture
    def make_match_with_penalties(self):
        from beets.autotag.distance import Distance
        from beets.autotag.hooks import AlbumMatch

        def _make(penalties):
            """Create an AlbumMatch with multiple penalty keys.

            penalties is a list of (key, value) tuples.
            """
            dist = Distance()
            for key, value in penalties:
                dist.add(key, value)
            match = AlbumMatch(dist, None, {}, [], [])
            return match

        return _make

    @pytest.mark.parametrize(
        "distance_value,expected_rec",
        [
            (0.0, Recommendation.strong),
            (0.01, Recommendation.strong),
            (0.0399, Recommendation.strong),
            (0.04, Recommendation.medium),
            (0.0401, Recommendation.medium),
            (0.15, Recommendation.medium),
            (0.2499, Recommendation.medium),
            (0.25, Recommendation.medium),
            (0.2501, Recommendation.low),
            (0.5, Recommendation.low),
            (0.99, Recommendation.low),
            (1.0, Recommendation.low),
        ],
    )
    def test_threshold_boundary_precision(
        self, make_match_with_penalties, distance_value, expected_rec
    ):
        """Test recommendation thresholds with high precision boundary values."""
        from beets.autotag.match import _recommendation

        results = [make_match_with_penalties([("album", distance_value)])]
        rec = _recommendation(results)
        assert rec == expected_rec, (
            f"Distance {distance_value} should give {expected_rec}, "
            f"got {rec}"
        )

    @pytest.mark.parametrize(
        "penalties,expected_rec",
        [
            ([("album", 0.02), ("artist", 0.01)], Recommendation.strong),
            ([("album", 0.1), ("artist", 0.1)], Recommendation.medium),
            ([("album", 0.2), ("artist", 0.2)], Recommendation.medium),
            ([("album", 0.3), ("artist", 0.3)], Recommendation.low),
        ],
    )
    def test_multiple_penalty_keys(
        self, make_match_with_penalties, penalties, expected_rec
    ):
        """Test recommendation with multiple penalty keys contributing."""
        from beets.autotag.match import _recommendation

        results = [make_match_with_penalties(penalties)]
        rec = _recommendation(results)
        assert rec == expected_rec

    @pytest.mark.parametrize(
        "penalty_key,max_rec_value,base_distance,expected_rec",
        [
            ("album", "strong", 0.02, Recommendation.strong),
            ("album", "medium", 0.02, Recommendation.medium),
            ("album", "low", 0.02, Recommendation.low),
            ("album", "none", 0.02, Recommendation.none),
            ("artist", "medium", 0.02, Recommendation.medium),
            ("artist", "low", 0.02, Recommendation.low),
        ],
    )
    def test_max_rec_downgrade_for_different_penalties(
        self,
        make_match_with_penalties,
        penalty_key,
        max_rec_value,
        base_distance,
        expected_rec,
    ):
        """Test max_rec downgrade works for different penalty types."""
        from beets.autotag.match import _recommendation
        from beets import config as beets_config

        beets_config["match"]["max_rec"][penalty_key] = max_rec_value
        results = [make_match_with_penalties([(penalty_key, base_distance)])]
        rec = _recommendation(results)
        assert rec == expected_rec

    def test_max_rec_downgrade_for_applied_penalty(
        self, make_match_with_penalties
    ):
        """Test max_rec downgrades for the applied penalty key.

        When max_rec is set for a penalty key that is actually applied
        (has a non-zero value), the recommendation should be downgraded.
        """
        from beets.autotag.match import _recommendation
        from beets import config as beets_config

        beets_config["match"]["max_rec"]["album"] = "low"
        results = [make_match_with_penalties([("album", 0.02)])]
        rec = _recommendation(results)
        assert rec == Recommendation.low

    def test_max_rec_ignored_for_non_applied_penalty(
        self, make_match_with_penalties
    ):
        """Test max_rec is ignored for penalty keys that are not applied.

        When max_rec is set for a penalty key that is not applied
        (has zero value), the recommendation should not be affected.
        """
        from beets.autotag.match import _recommendation
        from beets import config as beets_config

        beets_config["match"]["max_rec"]["album"] = "low"
        results = [make_match_with_penalties([("artist", 0.02)])]
        rec = _recommendation(results)
        assert rec == Recommendation.strong

    @pytest.mark.parametrize(
        "first_dist,second_dist,gap_thresh,expected_rec",
        [
            (0.3, 0.55, 0.25, Recommendation.low),
            (0.3, 0.5499, 0.25, Recommendation.none),
            (0.3, 0.5501, 0.25, Recommendation.low),
            (0.5, 1.0, 0.5, Recommendation.low),
            (0.5, 0.999, 0.5, Recommendation.none),
        ],
    )
    def test_gap_threshold_precision(
        self,
        make_match_with_penalties,
        first_dist,
        second_dist,
        gap_thresh,
        expected_rec,
    ):
        """Test gap threshold with high precision values."""
        from beets.autotag.match import _recommendation
        from beets import config as beets_config

        original_gap = beets_config["match"]["rec_gap_thresh"].get()
        beets_config["match"]["rec_gap_thresh"] = gap_thresh
        try:
            results = [
                make_match_with_penalties([("album", first_dist)]),
                make_match_with_penalties([("album", second_dist)]),
            ]
            rec = _recommendation(results)
            assert rec == expected_rec, (
                f"Gap {second_dist - first_dist} with thresh {gap_thresh} "
                f"should give {expected_rec}, got {rec}"
            )
        finally:
            beets_config["match"]["rec_gap_thresh"] = original_gap

    def test_three_candidates_with_varying_gaps(
        self, make_match_with_penalties
    ):
        """Test recommendation with more than two candidates."""
        from beets.autotag.match import _recommendation

        results = [
            make_match_with_penalties([("album", 0.3)]),
            make_match_with_penalties([("album", 0.56)]),
            make_match_with_penalties([("album", 0.57)]),
        ]
        rec = _recommendation(results)
        assert rec == Recommendation.low

    def test_three_candidates_no_clear_gap(self, make_match_with_penalties):
        """Test three candidates with no clear gap between any."""
        from beets.autotag.match import _recommendation

        results = [
            make_match_with_penalties([("album", 0.3)]),
            make_match_with_penalties([("album", 0.31)]),
            make_match_with_penalties([("album", 0.32)]),
        ]
        rec = _recommendation(results)
        assert rec == Recommendation.none

    def test_single_candidate_above_medium_threshold(
        self, make_match_with_penalties
    ):
        """Test single candidate above medium threshold gives low rec."""
        from beets.autotag.match import _recommendation

        results = [make_match_with_penalties([("album", 0.5)])]
        rec = _recommendation(results)
        assert rec == Recommendation.low

    def test_strong_match_with_applied_penalty_downgrade(
        self, make_match_with_penalties
    ):
        """Test that a strong match can be downgraded by applied penalties."""
        from beets.autotag.match import _recommendation
        from beets import config as beets_config

        beets_config["match"]["max_rec"]["album"] = "none"
        results = [make_match_with_penalties([("album", 0.01)])]
        rec = _recommendation(results)
        assert rec == Recommendation.none

    def test_medium_match_downgraded_to_low(
        self, make_match_with_penalties
    ):
        """Test medium recommendation downgraded to low by max_rec."""
        from beets.autotag.match import _recommendation
        from beets import config as beets_config

        beets_config["match"]["max_rec"]["artist"] = "low"
        results = [make_match_with_penalties([("artist", 0.1)])]
        rec = _recommendation(results)
        assert rec == Recommendation.low

    def test_recommendation_hierarchy(self, make_match_with_penalties):
        """Test that recommendation levels follow expected hierarchy."""
        from beets.autotag.match import _recommendation

        assert Recommendation.strong > Recommendation.medium
        assert Recommendation.medium > Recommendation.low
        assert Recommendation.low > Recommendation.none

        strong_match = make_match_with_penalties([("album", 0.01)])
        medium_match = make_match_with_penalties([("album", 0.1)])
        low_match = make_match_with_penalties([("album", 0.5)])

        assert _recommendation([strong_match]) == Recommendation.strong
        assert _recommendation([medium_match]) == Recommendation.medium
        assert _recommendation([low_match]) == Recommendation.low

        assert _recommendation([strong_match]) > _recommendation([medium_match])
        assert _recommendation([medium_match]) > _recommendation([low_match])
