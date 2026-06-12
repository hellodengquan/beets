from __future__ import annotations

import datetime
import re
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from functools import cache, total_ordering
from typing import TYPE_CHECKING, Any

from jellyfish import levenshtein_distance
from unidecode import unidecode

from beets import config, logging, metadata_plugins, plugins
from beets.util import as_string, cached_classproperty, get_most_common_tags
from beets.util.color import colorize

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from beets.library import Item
    from beets.util.color import ColorName

    from .hooks import AlbumInfo, TrackInfo

log = logging.getLogger("beets")

# Candidate distance scoring.

# Artist signals that indicate "various artists". These are used at the
# album level to determine whether a given release is likely a VA
# release and also on the track level to to remove the penalty for
# differing artists.
VA_ARTISTS = ("", "various artists", "various", "va", "unknown")

# Parameters for string distance function.
# Words that can be moved to the end of a string using a comma.
SD_END_WORDS = ["the", "a", "an"]
# Reduced weights for certain portions of the string.
SD_PATTERNS = [
    (r"^the ", 0.1),
    (r"[\[\(]?(ep|single)[\]\)]?", 0.0),
    (r"[\[\(]?(featuring|feat|ft)[\. :].+", 0.1),
    (r"\(.*?\)", 0.3),
    (r"\[.*?\]", 0.3),
    (r"(, )?(pt\.|part) .+", 0.2),
]
# Replacements to use before testing distance.
SD_REPLACE = [(r"&", "and")]


def _string_dist_basic(str1: str, str2: str) -> float:
    """Basic edit distance between two strings, ignoring
    non-alphanumeric characters and case. Comparisons are based on a
    transliteration/lowering to ASCII characters. Normalized by string
    length.
    """
    assert isinstance(str1, str)
    assert isinstance(str2, str)
    str1 = as_string(unidecode(str1))
    str2 = as_string(unidecode(str2))
    str1 = re.sub(r"[^a-z0-9]", "", str1.lower())
    str2 = re.sub(r"[^a-z0-9]", "", str2.lower())
    if not str1 and not str2:
        return 0.0
    return levenshtein_distance(str1, str2) / float(max(len(str1), len(str2)))


def string_dist(str1: str | None, str2: str | None) -> float:
    """Gives an "intuitive" edit distance between two strings. This is
    an edit distance, normalized by the string length, with a number of
    tweaks that reflect intuition about text.
    """
    if str1 is None and str2 is None:
        return 0.0
    if str1 is None or str2 is None:
        return 1.0

    str1 = str1.lower()
    str2 = str2.lower()

    # Don't penalize strings that move certain words to the end. For
    # example, "the something" should be considered equal to
    # "something, the".
    for word in SD_END_WORDS:
        if str1.endswith(f", {word}"):
            str1 = f"{word} {str1[: -len(word) - 2]}"
        if str2.endswith(f", {word}"):
            str2 = f"{word} {str2[: -len(word) - 2]}"

    # Perform a couple of basic normalizing substitutions.
    for pat, repl in SD_REPLACE:
        str1 = re.sub(pat, repl, str1)
        str2 = re.sub(pat, repl, str2)

    # Change the weight for certain string portions matched by a set
    # of regular expressions. We gradually change the strings and build
    # up penalties associated with parts of the string that were
    # deleted.
    base_dist = _string_dist_basic(str1, str2)
    penalty = 0.0
    for pat, weight in SD_PATTERNS:
        # Get strings that drop the pattern.
        case_str1 = re.sub(pat, "", str1)
        case_str2 = re.sub(pat, "", str2)

        if case_str1 != str1 or case_str2 != str2:
            # If the pattern was present (i.e., it is deleted in the
            # the current case), recalculate the distances for the
            # modified strings.
            case_dist = _string_dist_basic(case_str1, case_str2)
            case_delta = max(0.0, base_dist - case_dist)
            if case_delta == 0.0:
                continue

            # Shift our baseline strings down (to avoid rematching the
            # same part of the string) and add a scaled distance
            # amount to the penalties.
            str1 = case_str1
            str2 = case_str2
            base_dist = case_dist
            penalty += weight * case_delta

    return base_dist + penalty


@total_ordering
class Distance:
    """Keeps track of multiple distance penalties. Provides a single
    weighted distance for all penalties as well as a weighted distance
    for each individual penalty.
    """

    def __init__(self) -> None:
        self._penalties: dict[str, list[float]] = {}
        self.tracks: dict[TrackInfo, Distance] = {}

    @cached_classproperty
    def _weights(cls) -> dict[str, float]:
        """A dictionary from keys to floating-point weights."""
        weights_view = config["match"]["distance_weights"]
        weights = {}
        for key in weights_view.keys():
            weights[key] = weights_view[key].as_number()
        return weights

    @property
    def generic_penalty_keys(self) -> list[str]:
        return [
            k.replace("album_", "").replace("track_", "").replace("_", " ")
            for k in self._penalties
            if self[k]
        ]

    # Access the components and their aggregates.

    @property
    def distance(self) -> float:
        """Return a weighted and normalized distance across all
        penalties.
        """
        dist_max = self.max_distance
        if dist_max:
            return self.raw_distance / self.max_distance
        return 0.0

    @property
    def max_distance(self) -> float:
        """Return the maximum distance penalty (normalization factor)."""
        dist_max = 0.0
        for key, penalty in self._penalties.items():
            dist_max += len(penalty) * self._weights[key]
        return dist_max

    @property
    def raw_distance(self) -> float:
        """Return the raw (denormalized) distance."""
        dist_raw = 0.0
        for key, penalty in self._penalties.items():
            dist_raw += sum(penalty) * self._weights[key]
        return dist_raw

    @property
    def color(self) -> ColorName:
        if self.distance <= config["match"]["strong_rec_thresh"].as_number():
            return "text_success"
        if self.distance <= config["match"]["medium_rec_thresh"].as_number():
            return "text_warning"
        return "text_error"

    @property
    def string(self) -> str:
        return colorize(self.color, f"{(1 - self.distance) * 100:.1f}%")

    def items(self) -> list[tuple[str, float]]:
        """Return a list of (key, dist) pairs, with `dist` being the
        weighted distance, sorted from highest to lowest. Does not
        include penalties with a zero value.
        """
        list_ = []
        for key in self._penalties:
            dist = self[key]
            if dist:
                list_.append((key, dist))
        # Convert distance into a negative float we can sort items in
        # ascending order (for keys, when the penalty is equal) and
        # still get the items with the biggest distance first.
        return sorted(
            list_, key=lambda key_and_dist: (-key_and_dist[1], key_and_dist[0])
        )

    def __hash__(self) -> int:
        return id(self)

    def __eq__(self, other) -> bool:
        return self.distance == other

    # Behave like a float.

    def __lt__(self, other) -> bool:
        return self.distance < other

    def __float__(self) -> float:
        return self.distance

    def __sub__(self, other) -> float:
        return self.distance - other

    def __rsub__(self, other) -> float:
        return other - self.distance

    def __str__(self) -> str:
        return f"{self.distance:.2f}"

    # Behave like a dict.

    def __getitem__(self, key) -> float:
        """Returns the weighted distance for a named penalty."""
        dist = sum(self._penalties[key]) * self._weights[key]
        dist_max = self.max_distance
        if dist_max:
            return dist / dist_max
        return 0.0

    def __iter__(self) -> Iterator[tuple[str, float]]:
        return iter(self.items())

    def __len__(self) -> int:
        return len(self.items())

    def keys(self) -> list[str]:
        return [key for key, _ in self.items()]

    def update(self, dist: Distance):
        """Adds all the distance penalties from `dist`."""
        if not isinstance(dist, Distance):
            raise ValueError(
                f"`dist` must be a Distance object, not {type(dist)}"
            )
        for key, penalties in dist._penalties.items():
            self._penalties.setdefault(key, []).extend(penalties)

    # Adding components.

    def _eq(self, value1: re.Pattern[str] | Any, value2: Any) -> bool:
        """Returns True if `value1` is equal to `value2`. `value1` may
        be a compiled regular expression, in which case it will be
        matched against `value2`.
        """
        if isinstance(value1, re.Pattern):
            return bool(value1.match(value2))
        return value1 == value2

    def add(self, key: str, dist: float):
        """Adds a distance penalty. `key` must correspond with a
        configured weight setting. `dist` must be a float between 0.0
        and 1.0, and will be added to any existing distance penalties
        for the same key.
        """
        if not 0.0 <= dist <= 1.0:
            raise ValueError(f"`dist` must be between 0.0 and 1.0, not {dist}")
        self._penalties.setdefault(key, []).append(dist)

    def add_equality(
        self, key: str, value: Any, options: list[Any] | tuple[Any, ...] | Any
    ):
        """Adds a distance penalty of 1.0 if `value` doesn't match any
        of the values in `options`. If an option is a compiled regular
        expression, it will be considered equal if it matches against
        `value`.
        """
        if not isinstance(options, (list, tuple)):
            options = [options]
        for opt in options:
            if self._eq(opt, value):
                dist = 0.0
                break
        else:
            dist = 1.0
        self.add(key, dist)

    def add_expr(self, key: str, expr: bool):
        """Adds a distance penalty of 1.0 if `expr` evaluates to True,
        or 0.0.
        """
        if expr:
            self.add(key, 1.0)
        else:
            self.add(key, 0.0)

    def add_number(self, key: str, number1: int, number2: int):
        """Adds a distance penalty of 1.0 for each number of difference
        between `number1` and `number2`, or 0.0 when there is no
        difference. Use this when there is no upper limit on the
        difference between the two numbers.
        """
        diff = abs(number1 - number2)
        if diff:
            for i in range(diff):
                self.add(key, 1.0)
        else:
            self.add(key, 0.0)

    def add_priority(
        self, key: str, value: Any, options: list[Any] | tuple[Any, ...] | Any
    ):
        """Adds a distance penalty that corresponds to the position at
        which `value` appears in `options`. A distance penalty of 0.0
        for the first option, or 1.0 if there is no matching option. If
        an option is a compiled regular expression, it will be
        considered equal if it matches against `value`.
        """
        if not isinstance(options, (list, tuple)):
            options = [options]
        unit = 1.0 / (len(options) or 1)
        for i, opt in enumerate(options):
            if self._eq(opt, value):
                dist = i * unit
                break
        else:
            dist = 1.0
        self.add(key, dist)

    def add_ratio(self, key: str, number1: int | float, number2: int | float):
        """Adds a distance penalty for `number1` as a ratio of `number2`.
        `number1` is bound at 0 and `number2`.
        """
        number = float(max(min(number1, number2), 0))
        if number2:
            dist = number / number2
        else:
            dist = 0.0
        self.add(key, dist)

    def add_string(self, key: str, str1: str | None, str2: str | None):
        """Adds a distance penalty based on the edit distance between
        `str1` and `str2`.
        """
        dist = string_dist(str1, str2)
        self.add(key, dist)

    def add_data_source(self, before: str | None, after: str | None) -> None:
        if before != after and (
            before or len(metadata_plugins.find_metadata_source_plugins()) > 1
        ):
            self.add("data_source", metadata_plugins.get_penalty(after))


@cache
def get_track_length_grace() -> float:
    """Get cached grace period for track length matching."""
    return config["match"]["track_length_grace"].as_number()


@cache
def get_track_length_max() -> float:
    """Get cached maximum track length for track length matching."""
    return config["match"]["track_length_max"].as_number()


def track_index_changed(item: Item, track_info: TrackInfo) -> bool:
    """Returns True if the item and track info index is different. Tolerates
    per disc and per release numbering.
    """
    return item.track not in (track_info.medium_index, track_info.index)


def track_distance(
    item: Item, track_info: TrackInfo, incl_artist: bool = False
) -> Distance:
    """Determines the significance of a track metadata change. Returns a
    Distance object. `incl_artist` indicates that a distance component should
    be included for the track artist (i.e., for various-artist releases).

    ``track_length_grace`` and ``track_length_max`` configuration options are
    cached because this function is called many times during the matching
    process and their access comes with a performance overhead.
    """
    dist = Distance()

    # Length.
    if info_length := track_info.length:
        diff = abs(item.length - info_length) - get_track_length_grace()
        dist.add_ratio("track_length", diff, get_track_length_max())

    # Title.
    dist.add_string("track_title", item.title, track_info.title)

    # Artist. Only check if there is actually an artist in the track data.
    if (
        incl_artist
        and track_info.artist
        and item.artist.lower() not in VA_ARTISTS
    ):
        dist.add_string("track_artist", item.artist, track_info.artist)

    # Track index.
    if track_info.index and item.track:
        dist.add_expr("track_index", track_index_changed(item, track_info))

    # Track ID.
    if item.mb_trackid:
        dist.add_expr("track_id", item.mb_trackid != track_info.track_id)

    # Penalize mismatching disc numbers.
    if track_info.medium and item.disc:
        dist.add_expr("medium", item.disc != track_info.medium)

    dist.add_data_source(item.get("data_source"), track_info.data_source)

    return dist


def distance(
    items: Sequence[Item],
    album_info: AlbumInfo,
    item_info_pairs: list[tuple[Item, TrackInfo]],
) -> Distance:
    """Determines how "significant" an album metadata change would be.
    Returns a Distance object. `album_info` is an AlbumInfo object
    reflecting the album to be compared. `items` is a sequence of all
    Item objects that will be matched (order is not important).
    `mapping` is a dictionary mapping Items to TrackInfo objects; the
    keys are a subset of `items` and the values are a subset of
    `album_info.tracks`.
    """
    likelies, _ = get_most_common_tags(items)

    dist = Distance()

    # Artist, if not various.
    if not album_info.va:
        dist.add_string("artist", likelies["artist"], album_info.artist)

    # Album.
    dist.add_string("album", likelies["album"], album_info.album)

    preferred_config = config["match"]["preferred"]
    # Current or preferred media.
    if album_info.media:
        # Preferred media options.
        media_patterns: Sequence[str] = preferred_config["media"].as_str_seq()
        options = [
            re.compile(rf"(\d+x)?({pat})", re.I) for pat in media_patterns
        ]
        if options:
            dist.add_priority("media", album_info.media, options)
        # Current media.
        elif likelies["media"]:
            dist.add_equality("media", album_info.media, likelies["media"])

    # Mediums.
    if likelies["disctotal"] and album_info.mediums:
        dist.add_number("mediums", likelies["disctotal"], album_info.mediums)

    # Prefer earliest release.
    if album_info.year and preferred_config["original_year"]:
        # Assume 1889 (earliest first gramophone discs) if we don't know the
        # original year.
        original = album_info.original_year or 1889
        diff = abs(album_info.year - original)
        diff_max = abs(datetime.date.today().year - original)
        dist.add_ratio("year", diff, diff_max)
    # Year.
    elif likelies["year"] and album_info.year:
        if likelies["year"] in (album_info.year, album_info.original_year):
            # No penalty for matching release or original year.
            dist.add("year", 0.0)
        elif album_info.original_year:
            # Prefer matchest closest to the release year.
            diff = abs(likelies["year"] - album_info.year)
            diff_max = abs(
                datetime.date.today().year - album_info.original_year
            )
            dist.add_ratio("year", diff, diff_max)
        else:
            # Full penalty when there is no original year.
            dist.add("year", 1.0)

    # Preferred countries.
    country_patterns: Sequence[str] = preferred_config["countries"].as_str_seq()
    options = [re.compile(pat, re.I) for pat in country_patterns]
    if album_info.country and options:
        dist.add_priority("country", album_info.country, options)
    # Country.
    elif likelies["country"] and album_info.country:
        dist.add_string("country", likelies["country"], album_info.country)

    # Label.
    if likelies["label"] and album_info.label:
        dist.add_string("label", likelies["label"], album_info.label)

    # Catalog number.
    if likelies["catalognum"] and album_info.catalognum:
        dist.add_string(
            "catalognum", likelies["catalognum"], album_info.catalognum
        )

    # Disambiguation.
    if likelies["albumdisambig"] and album_info.albumdisambig:
        dist.add_string(
            "albumdisambig", likelies["albumdisambig"], album_info.albumdisambig
        )

    # Album ID.
    if likelies["mb_albumid"]:
        dist.add_equality(
            "album_id", likelies["mb_albumid"], album_info.album_id
        )

    # Tracks.
    dist.tracks = {}
    for item, track in item_info_pairs:
        dist.tracks[track] = track_distance(item, track, album_info.va)
        dist.add("tracks", dist.tracks[track].distance)

    # Missing tracks.
    for _ in range(len(album_info.tracks) - len(item_info_pairs)):
        dist.add("missing_tracks", 1.0)

    # Unmatched tracks.
    for _ in range(len(items) - len(item_info_pairs)):
        dist.add("unmatched_tracks", 1.0)

    dist.add_data_source(likelies["data_source"], album_info.data_source)

    return dist


# ============================================================================
# Scoring Pipeline Architecture
# ============================================================================


def _safe_plugin_send(event: str, **kwargs: Any) -> list[Any]:
    """Safely emit a plugin event, catching and logging any exceptions.

    Provides an extra layer of defensive protection around ``plugins.send``
    for the scoring pipeline. While the core ``send()`` already wraps each
    listener individually, this wrapper ensures that any *other* exception
    (e.g. from argument construction) also cannot break the pipeline.

    :param event: Name of the plugin event to dispatch.
    :param kwargs: Keyword arguments forwarded to every listener.
    :returns: List of non-None return values from successful listeners.
    """
    try:
        return plugins.send(event, **kwargs)
    except Exception as exc:
        log.warning(
            "Scoring pipeline: failed to emit event '{}': {}: {}",
            event,
            type(exc).__name__,
            exc,
        )
        log.debug("Plugin event dispatch error details:", exc_info=True)
        return []


class ScoringStage(Enum):
    """Defines the sequential stages of the candidate scoring pipeline.

    Plugins can hook into each stage to customize scoring behavior. The stages
    are executed in the defined order, with each stage building upon the
    previous stage's results.
    """

    INITIALIZE = auto()
    ALBUM_METADATA = auto()
    PREFERRED_METADATA = auto()
    YEAR_METADATA = auto()
    TRACK_MATCHING = auto()
    TRACK_COMPLETENESS = auto()
    DATA_SOURCE = auto()
    PLUGIN_CUSTOM = auto()
    FINALIZE = auto()


@dataclass
class TrackScoringContext:
    """Context for scoring a single track candidate.

    Attributes:
        item: The library item being matched.
        track_info: The candidate track metadata.
        incl_artist: Whether to include track artist in scoring.
        distance: The Distance object being built up during scoring.
        stage: The current scoring stage being executed.
        extra: Extra data for plugins to attach custom information.
    """

    item: Item
    track_info: TrackInfo
    incl_artist: bool
    distance: Distance = field(default_factory=Distance)
    stage: ScoringStage = ScoringStage.INITIALIZE
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AlbumScoringContext:
    """Context for scoring an album candidate.

    Attributes:
        items: The library items being matched (the album's tracks).
        album_info: The candidate album metadata.
        item_info_pairs: List of (item, track_info) matched pairs.
        likelies: Most common tag values from the input items.
        distance: The Distance object being built up during scoring.
        stage: The current scoring stage being executed.
        extra: Extra data for plugins to attach custom information.
    """

    items: Sequence[Item]
    album_info: AlbumInfo
    item_info_pairs: list[tuple[Item, TrackInfo]]
    likelies: dict[str, Any]
    distance: Distance = field(default_factory=Distance)
    stage: ScoringStage = ScoringStage.INITIALIZE
    extra: dict[str, Any] = field(default_factory=dict)


class TrackScoringPipeline:
    """Pipeline for scoring individual track candidates.

    Encapsulates the full sequence of scoring stages for a single track, with
    plugin hooks at each stage for customization.

    Usage::

        pipeline = TrackScoringPipeline(item, track_info, incl_artist=True)
        distance = pipeline.score()

    Or with a pre-built context::

        distance = TrackScoringPipeline.run(ctx)
    """

    def __init__(
        self,
        item: Item,
        track_info: TrackInfo,
        incl_artist: bool = False,
    ) -> None:
        self.ctx = TrackScoringContext(
            item=item,
            track_info=track_info,
            incl_artist=incl_artist,
        )

    @classmethod
    def run(cls, ctx: TrackScoringContext) -> Distance:
        """Execute the full scoring pipeline on a pre-built context."""
        pipeline = cls.__new__(cls)
        pipeline.ctx = ctx
        return pipeline.score()

    def score(self) -> Distance:
        """Execute all scoring stages sequentially.

        Each stage is wrapped in its own ``try``/``except`` so a failure in
        one stage (or its plugin hooks) is logged and skipped rather than
        aborting the entire scoring pipeline. Per-stage wall-clock time is
        recorded into ``context.extra['stage_durations']`` so slow stages
        (typically due to a slow plugin hook) can be easily diagnosed.
        """
        stage_methods = [
            (ScoringStage.INITIALIZE, self._stage_initialize),
            (ScoringStage.ALBUM_METADATA, self._stage_track_metadata),
            (ScoringStage.DATA_SOURCE, self._stage_data_source),
            (ScoringStage.PLUGIN_CUSTOM, self._stage_plugin_custom),
            (ScoringStage.FINALIZE, self._stage_finalize),
        ]
        durations: dict[str, float] = {}
        for stage, method in stage_methods:
            self.ctx.stage = stage
            start = time.perf_counter()
            try:
                method()
            except Exception as exc:
                log.warning(
                    "Track scoring stage '%s' failed: %s: %s",
                    stage.name,
                    type(exc).__name__,
                    exc,
                )
                log.debug(
                    "Exception during track scoring stage %s:",
                    stage.name,
                    exc_info=True,
                )
            finally:
                elapsed = time.perf_counter() - start
                durations[stage.name] = elapsed
                if elapsed > 0.5:  # Half a second is slow for a stage.
                    log.debug(
                        "Track scoring stage '%s' took %.2fs",
                        stage.name,
                        elapsed,
                    )
        self.ctx.extra.setdefault("stage_durations", {}).update(
            {"track_" + k: v for k, v in durations.items()}
        )
        return self.ctx.distance

    def _stage_initialize(self) -> None:
        """Initialize scoring context (hook point for plugins)."""
        _safe_plugin_send("track_distance_calculated", context=self.ctx)

    def _stage_track_metadata(self) -> None:
        """Score core track metadata: length, title, artist, index, ID, medium."""
        item, track = self.ctx.item, self.ctx.track_info
        dist = self.ctx.distance

        if info_length := track.length:
            diff = abs(item.length - info_length) - get_track_length_grace()
            dist.add_ratio("track_length", diff, get_track_length_max())

        dist.add_string("track_title", item.title, track.title)

        if (
            self.ctx.incl_artist
            and track.artist
            and item.artist.lower() not in VA_ARTISTS
        ):
            dist.add_string("track_artist", item.artist, track.artist)

        if track.index and item.track:
            dist.add_expr("track_index", track_index_changed(item, track))

        if item.mb_trackid:
            dist.add_expr("track_id", item.mb_trackid != track.track_id)

        if track.medium and item.disc:
            dist.add_expr("medium", item.disc != track.medium)

    def _stage_data_source(self) -> None:
        """Score data source mismatch."""
        self.ctx.distance.add_data_source(
            self.ctx.item.get("data_source"),
            self.ctx.track_info.data_source,
        )

    def _stage_plugin_custom(self) -> None:
        """Allow plugins to inject custom penalties or adjust scoring."""
        _safe_plugin_send("track_candidate_scored", context=self.ctx)

    def _stage_finalize(self) -> None:
        """Finalize scoring (post-processing hook for plugins)."""
        _safe_plugin_send("track_distance_calculated", context=self.ctx)


class AlbumScoringPipeline:
    """Pipeline for scoring album candidates.

    Encapsulates the full sequence of scoring stages for an album, breaking
    the monolithic ``distance()`` function into composable stages with plugin
    hooks at each boundary.

    Stages:
        1. **INITIALIZE**: Set up the scoring context.
        2. **ALBUM_METADATA**: Score artist, album, label, catalog, etc.
        3. **PREFERRED_METADATA**: Score preferred media, country options.
        4. **YEAR_METADATA**: Score release year and original year preference.
        5. **TRACK_MATCHING**: Score individual track matches.
        6. **TRACK_COMPLETENESS**: Score missing and unmatched tracks.
        7. **DATA_SOURCE**: Score data source mismatch penalty.
        8. **PLUGIN_CUSTOM**: Plugin custom scoring hook.
        9. **FINALIZE**: Post-processing and final hook.

    Usage::

        pipeline = AlbumScoringPipeline(items, album_info, item_info_pairs)
        distance = pipeline.score()

    Or with a pre-built context::

        distance = AlbumScoringPipeline.run(ctx)
    """

    def __init__(
        self,
        items: Sequence[Item],
        album_info: AlbumInfo,
        item_info_pairs: list[tuple[Item, TrackInfo]],
    ) -> None:
        likelies, _ = get_most_common_tags(items)
        self.ctx = AlbumScoringContext(
            items=items,
            album_info=album_info,
            item_info_pairs=item_info_pairs,
            likelies=likelies,
        )

    @classmethod
    def run(cls, ctx: AlbumScoringContext) -> Distance:
        """Execute the full scoring pipeline on a pre-built context."""
        pipeline = cls.__new__(cls)
        pipeline.ctx = ctx
        return pipeline.score()

    def score(self) -> Distance:
        """Execute all scoring stages sequentially.

        Each stage is wrapped in its own ``try``/``except`` so a failure in
        one stage (or its plugin hooks) is logged and skipped rather than
        aborting the entire scoring pipeline. Per-stage wall-clock time is
        recorded into ``context.extra['stage_durations']`` so slow stages
        (typically due to a slow plugin hook) can be easily diagnosed.
        """
        stage_methods = [
            (ScoringStage.INITIALIZE, self._stage_initialize),
            (ScoringStage.ALBUM_METADATA, self._stage_album_metadata),
            (ScoringStage.PREFERRED_METADATA, self._stage_preferred_metadata),
            (ScoringStage.YEAR_METADATA, self._stage_year_metadata),
            (ScoringStage.TRACK_MATCHING, self._stage_track_matching),
            (ScoringStage.TRACK_COMPLETENESS, self._stage_track_completeness),
            (ScoringStage.DATA_SOURCE, self._stage_data_source),
            (ScoringStage.PLUGIN_CUSTOM, self._stage_plugin_custom),
            (ScoringStage.FINALIZE, self._stage_finalize),
        ]
        durations: dict[str, float] = {}
        for stage, method in stage_methods:
            self.ctx.stage = stage
            start = time.perf_counter()
            try:
                method()
            except Exception as exc:
                log.warning(
                    "Album scoring stage '%s' failed: %s: %s",
                    stage.name,
                    type(exc).__name__,
                    exc,
                )
                log.debug(
                    "Exception during album scoring stage %s:",
                    stage.name,
                    exc_info=True,
                )
            finally:
                elapsed = time.perf_counter() - start
                durations[stage.name] = elapsed
                if elapsed > 1.0:  # One second is slow for an album stage.
                    log.debug(
                        "Album scoring stage '%s' took %.2fs",
                        stage.name,
                        elapsed,
                    )
        self.ctx.extra.setdefault("stage_durations", {}).update(
            {"album_" + k: v for k, v in durations.items()}
        )
        return self.ctx.distance

    def _stage_initialize(self) -> None:
        """Initialize the scoring context.

        Hook point for plugins to set up custom state before scoring begins.
        """
        _safe_plugin_send("album_distance_calculated", context=self.ctx)

    def _stage_album_metadata(self) -> None:
        """Score core album metadata fields.

        Scores: artist (non-VA), album title, label, catalog number,
        album disambiguation, and album ID.
        """
        album_info = self.ctx.album_info
        likelies = self.ctx.likelies
        dist = self.ctx.distance

        if not album_info.va:
            dist.add_string("artist", likelies["artist"], album_info.artist)

        dist.add_string("album", likelies["album"], album_info.album)

        if likelies["disctotal"] and album_info.mediums:
            dist.add_number("mediums", likelies["disctotal"], album_info.mediums)

        if likelies["label"] and album_info.label:
            dist.add_string("label", likelies["label"], album_info.label)

        if likelies["catalognum"] and album_info.catalognum:
            dist.add_string(
                "catalognum", likelies["catalognum"], album_info.catalognum
            )

        if likelies["albumdisambig"] and album_info.albumdisambig:
            dist.add_string(
                "albumdisambig",
                likelies["albumdisambig"],
                album_info.albumdisambig,
            )

        if likelies["mb_albumid"]:
            dist.add_equality(
                "album_id", likelies["mb_albumid"], album_info.album_id
            )

    def _stage_preferred_metadata(self) -> None:
        """Score preferred/configured metadata preferences.

        Scores: media type (with regex patterns) and country (with patterns).
        Falls back to equality comparison if no preferred patterns are set.
        """
        album_info = self.ctx.album_info
        likelies = self.ctx.likelies
        dist = self.ctx.distance
        preferred_config = config["match"]["preferred"]

        if album_info.media:
            media_patterns: Sequence[str] = preferred_config["media"].as_str_seq()
            options = [
                re.compile(rf"(\d+x)?({pat})", re.I) for pat in media_patterns
            ]
            if options:
                dist.add_priority("media", album_info.media, options)
            elif likelies["media"]:
                dist.add_equality("media", album_info.media, likelies["media"])

        country_patterns: Sequence[str] = preferred_config["countries"].as_str_seq()
        options = [re.compile(pat, re.I) for pat in country_patterns]
        if album_info.country and options:
            dist.add_priority("country", album_info.country, options)
        elif likelies["country"] and album_info.country:
            dist.add_string("country", likelies["country"], album_info.country)

    def _stage_year_metadata(self) -> None:
        """Score release year and original-year preference.

        Handles multiple cases:
        - Prefer earliest release when configured.
        - Match against release year or original year.
        - Proximity-based penalty when original year is known.
        - Full penalty when no original year reference exists.
        """
        album_info = self.ctx.album_info
        likelies = self.ctx.likelies
        dist = self.ctx.distance
        preferred_config = config["match"]["preferred"]

        if album_info.year and preferred_config["original_year"]:
            original = album_info.original_year or 1889
            diff = abs(album_info.year - original)
            diff_max = abs(datetime.date.today().year - original)
            dist.add_ratio("year", diff, diff_max)
        elif likelies["year"] and album_info.year:
            if likelies["year"] in (album_info.year, album_info.original_year):
                dist.add("year", 0.0)
            elif album_info.original_year:
                diff = abs(likelies["year"] - album_info.year)
                diff_max = abs(
                    datetime.date.today().year - album_info.original_year
                )
                dist.add_ratio("year", diff, diff_max)
            else:
                dist.add("year", 1.0)

    def _stage_track_matching(self) -> None:
        """Score individual track matches via the TrackScoringPipeline.

        Delegates per-track scoring to :class:`TrackScoringPipeline` and
        aggregates each track's distance into the album score.
        """
        dist = self.ctx.distance
        dist.tracks = {}
        for item, track in self.ctx.item_info_pairs:
            track_ctx = TrackScoringContext(
                item=item,
                track_info=track,
                incl_artist=self.ctx.album_info.va,
            )
            track_dist = TrackScoringPipeline.run(track_ctx)
            dist.tracks[track] = track_dist
            dist.add("tracks", track_dist.distance)

    def _stage_track_completeness(self) -> None:
        """Score missing tracks (in candidate but not in items) and unmatched
        tracks (in items but not matched to a candidate track).
        """
        dist = self.ctx.distance
        for _ in range(len(self.ctx.album_info.tracks) - len(self.ctx.item_info_pairs)):
            dist.add("missing_tracks", 1.0)

        for _ in range(len(self.ctx.items) - len(self.ctx.item_info_pairs)):
            dist.add("unmatched_tracks", 1.0)

    def _stage_data_source(self) -> None:
        """Score data source mismatch penalty."""
        self.ctx.distance.add_data_source(
            self.ctx.likelies["data_source"],
            self.ctx.album_info.data_source,
        )

    def _stage_plugin_custom(self) -> None:
        """Custom plugin scoring stage.

        Plugins can modify the ``distance`` object or inspect the context
        to inject domain-specific penalties via the
        ``album_candidate_scored`` event.
        """
        _safe_plugin_send("album_candidate_scored", context=self.ctx)

    def _stage_finalize(self) -> None:
        """Finalize scoring and emit completion hook.

        Allows plugins to perform post-processing on the fully computed
        distance before it is returned to the caller.
        """
        _safe_plugin_send("album_distance_calculated", context=self.ctx)


def track_distance_pipeline(
    item: Item, track_info: TrackInfo, incl_artist: bool = False
) -> Distance:
    """Convenience wrapper for :class:`TrackScoringPipeline`.

    Maintains API compatibility with the original ``track_distance()``
    function while using the new pipeline internally.
    """
    return TrackScoringPipeline(item, track_info, incl_artist).score()


def distance_pipeline(
    items: Sequence[Item],
    album_info: AlbumInfo,
    item_info_pairs: list[tuple[Item, TrackInfo]],
) -> Distance:
    """Convenience wrapper for :class:`AlbumScoringPipeline`.

    Maintains API compatibility with the original ``distance()`` function
    while using the new pipeline internally.
    """
    return AlbumScoringPipeline(items, album_info, item_info_pairs).score()
