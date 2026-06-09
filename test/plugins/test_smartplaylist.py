# This file is part of beets.
# Copyright 2016, Bruno Cauet.
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

# TODO: Tests in this fire are very bad. Stop using Mocks in this module.

import os
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp
from unittest.mock import MagicMock, Mock, PropertyMock

import pytest

from beets import config
from beets.dbcore.sort import FixedFieldSort, MultipleSort, NullSort
from beets.library import Album, Item, parse_query_string
from beets.plugins import BeetsPlugin
from beets.test._common import item
from beets.test.helper import BeetsTestCase, IOMixin, PluginTestCase
from beets.ui import UserError
from beets.util import CHAR_REPLACE, syspath
from beetsplug.smartplaylist import SmartPlaylistPlugin

_p = pytest.param


class SmartPlaylistTest(BeetsTestCase):
    @staticmethod
    def _make_plugin():
        spl = SmartPlaylistPlugin()
        spl.register_listener = Mock()
        return spl

    def test_build_queries(self):
        spl = self._make_plugin()
        assert spl._matched_playlists == set()
        assert spl._unmatched_playlists == set()

        config["smartplaylist"]["playlists"].set([])
        spl.build_queries()
        assert spl._matched_playlists == set()
        assert spl._unmatched_playlists == set()

        config["smartplaylist"]["playlists"].set(
            [
                {"name": "foo", "query": "FOO foo"},
                {"name": "bar", "album_query": ["BAR bar1", "BAR bar2"]},
                {"name": "baz", "query": "BAZ baz", "album_query": "BAZ baz"},
            ]
        )
        spl.build_queries()
        assert spl._matched_playlists == set()
        foo_foo = parse_query_string("FOO foo", Item)
        baz_baz = parse_query_string("BAZ baz", Item)
        baz_baz2 = parse_query_string("BAZ baz", Album)
        # Multiple queries are now stored as a tuple of (query, sort) tuples
        bar_queries = tuple(
            [
                parse_query_string("BAR bar1", Album),
                parse_query_string("BAR bar2", Album),
            ]
        )
        assert spl._unmatched_playlists == {
            ("foo", foo_foo, (None, None)),
            ("baz", baz_baz, baz_baz2),
            ("bar", (None, None), (bar_queries, None)),
        }

    def test_build_queries_with_sorts(self):
        spl = self._make_plugin()
        config["smartplaylist"]["playlists"].set(
            [
                {"name": "no_sort", "query": "foo"},
                {"name": "one_sort", "query": "foo year+"},
                {"name": "only_empty_sorts", "query": ["foo", "bar"]},
                {"name": "one_non_empty_sort", "query": ["foo year+", "bar"]},
                {
                    "name": "multiple_sorts",
                    "query": ["foo year+", "bar genres-"],
                },
                {
                    "name": "mixed",
                    "query": ["foo year+", "bar", "baz genres+ id-"],
                },
            ]
        )

        spl.build_queries()

        # Multiple queries now return a tuple of (query, sort) tuples, not combined
        sorts = {}
        for name, (query_data, sort), _ in spl._unmatched_playlists:
            if isinstance(query_data, tuple):
                # Tuple of queries - each has its own sort
                sorts[name] = [s for _, s in query_data]
            else:
                sorts[name] = sort

        sort = FixedFieldSort  # short cut since we're only dealing with this
        assert sorts["no_sort"] == NullSort()
        assert sorts["one_sort"] == sort("year")
        # Multiple queries store individual sorts in the tuple
        assert all(isinstance(x, NullSort) for x in sorts["only_empty_sorts"])
        assert sorts["one_non_empty_sort"] == [sort("year"), NullSort()]
        assert sorts["multiple_sorts"] == [sort("year"), sort("genres", False)]
        assert sorts["mixed"] == [
            sort("year"),
            NullSort(),
            MultipleSort([sort("genres"), sort("id", False)]),
        ]

    def test_matches(self):
        spl = self._make_plugin()

        a = MagicMock(Album)
        i = MagicMock(Item)

        assert not spl.matches(i, None, None)
        assert not spl.matches(a, None, None)

        query = Mock()
        query.match.side_effect = {i: True}.__getitem__
        assert spl.matches(i, query, None)
        assert not spl.matches(a, query, None)

        a_query = Mock()
        a_query.match.side_effect = {a: True}.__getitem__
        assert not spl.matches(i, None, a_query)
        assert spl.matches(a, None, a_query)

        assert spl.matches(i, query, a_query)
        assert spl.matches(a, query, a_query)

        # Test with list of queries
        q1 = Mock()
        q1.match.return_value = False
        q2 = Mock()
        q2.match.side_effect = {i: True}.__getitem__
        queries_list = [(q1, None), (q2, None)]
        assert spl.matches(i, queries_list, None)
        assert not spl.matches(a, queries_list, None)

    def test_db_changes_item_triggers_item_query_playlists(self):
        """Any Item change should mark all playlists with item/album queries."""
        spl = self._make_plugin()

        nones = None, None
        pl1 = "1", ("q1", None), nones
        pl2 = "2", ("q2", None), nones
        pl3 = "3", nones, nones
        pl4 = "4", nones, ("aq1", None)
        pl5 = "5", ("q5", None), ("aq5", None)

        spl._unmatched_playlists = {pl1, pl2, pl3, pl4, pl5}
        spl._matched_playlists = set()

        i = MagicMock(Item)
        spl.db_change(None, i)

        assert spl._unmatched_playlists == {pl3}
        assert spl._matched_playlists == {pl1, pl2, pl4, pl5}
        assert spl._cli_exit_registered is True

    def test_db_changes_album_triggers_album_query_playlists(self):
        """Album change should mark playlists with album queries only."""
        spl = self._make_plugin()

        nones = None, None
        pl1 = "1", ("q1", None), nones
        pl2 = "2", nones, ("aq1", None)
        pl3 = "3", ("q3", None), ("aq3", None)
        pl4 = "4", nones, nones

        spl._unmatched_playlists = {pl1, pl2, pl3, pl4}
        spl._matched_playlists = set()

        a = MagicMock(Album)
        spl.db_change(None, a)

        assert spl._unmatched_playlists == {pl1, pl4}
        assert spl._matched_playlists == {pl2, pl3}

    def test_db_changes_multiple_updates_accumulate(self):
        """Multiple db changes should accumulate playlists to update."""
        spl = self._make_plugin()

        nones = None, None
        pl1 = "1", ("q1", None), nones
        pl2 = "2", ("q2", None), nones

        spl._unmatched_playlists = {pl1, pl2}
        spl._matched_playlists = set()

        i = MagicMock(Item)
        spl.db_change(None, i)

        assert spl._unmatched_playlists == set()
        assert spl._matched_playlists == {pl1, pl2}
        assert spl._cli_exit_registered is True

    def test_db_changes_after_update_playlists_reset(self):
        """After update_playlists, playlists should be returned to unmatched."""
        spl = self._make_plugin()

        nones = None, None
        pl1 = "1", ("q1", None), nones
        pl2 = "2", ("q2", None), nones

        spl._unmatched_playlists = {pl1, pl2}
        spl._matched_playlists = set()

        lib = Mock()
        lib.items.return_value = []
        lib.albums.return_value = []
        lib.replacements = CHAR_REPLACE

        i = MagicMock(Item)
        spl.db_change(None, i)
        assert spl._matched_playlists == {pl1, pl2}
        assert spl._unmatched_playlists == set()

        spl.update_playlists(lib)
        assert spl._unmatched_playlists == {pl1, pl2}
        assert spl._matched_playlists == set()

    def test_db_changes_item_added_triggers_update(self):
        """Newly added item should trigger playlist update."""
        spl = self._make_plugin()

        nones = None, None
        pl = "added.m3u", ("artist:NewArtist", None), nones
        spl._unmatched_playlists = {pl}
        spl._matched_playlists = set()

        new_item = MagicMock(Item)
        new_item.artist = "NewArtist"
        spl.db_change(None, new_item)

        assert pl in spl._matched_playlists
        assert spl._cli_exit_registered is True

    def test_db_changes_item_removed_triggers_update(self):
        """Removed item should trigger playlist update even if not currently matching."""
        spl = self._make_plugin()

        nones = None, None
        pl = "removed.m3u", ("genre:Rock", None), nones
        spl._unmatched_playlists = {pl}
        spl._matched_playlists = set()

        removed_item = MagicMock(Item)
        removed_item.genre = "Jazz"
        spl.db_change(None, removed_item)

        assert pl in spl._matched_playlists
        assert spl._cli_exit_registered is True

    def test_db_changes_item_renamed_triggers_update(self):
        """Item field change (rename/modify) should trigger playlist update."""
        spl = self._make_plugin()

        nones = None, None
        pl = "beatles.m3u", ("artist:Beatles", None), nones
        spl._unmatched_playlists = {pl}
        spl._matched_playlists = set()

        modified_item = MagicMock(Item)
        modified_item.artist = "The Beatles"
        spl.db_change(None, modified_item)

        assert pl in spl._matched_playlists
        assert spl._cli_exit_registered is True

    def test_playlist_update(self):
        spl = self._make_plugin()

        i = Mock(path=b"/tagada.mp3")
        i.evaluate_template.side_effect = lambda pl, *_: os.fsdecode(
            pl
        ).replace("$title", "ta:ga:da")

        lib = Mock()
        lib.replacements = CHAR_REPLACE
        lib.items.return_value = [i]
        lib.albums.return_value = []

        q = Mock()
        a_q = Mock()
        pl = b"$title-my<playlist>.m3u", (q, None), (a_q, None)
        spl._matched_playlists = {pl}

        dir = mkdtemp()
        config["smartplaylist"]["relative_to"] = False
        config["smartplaylist"]["playlist_dir"] = str(dir)
        try:
            spl.update_playlists(lib)
        except Exception:
            rmtree(syspath(dir))
            raise

        lib.items.assert_called_once_with(q, None)
        lib.albums.assert_called_once_with(a_q, None)

        m3u_filepath = Path(dir, "ta_ga_da-my_playlist_.m3u")
        assert m3u_filepath.exists()
        content = m3u_filepath.read_bytes()
        rmtree(syspath(dir))

        assert content == b"/tagada.mp3\n"

    def test_playlist_update_output_extm3u(self):
        spl = self._make_plugin()

        i = MagicMock()
        type(i).artist = PropertyMock(return_value="fake artist")
        type(i).title = PropertyMock(return_value="fake title")
        type(i).length = PropertyMock(return_value=300.123)
        type(i).path = PropertyMock(return_value=b"/tagada.mp3")
        i.evaluate_template.side_effect = lambda pl, *_: os.fsdecode(
            pl
        ).replace("$title", "ta:ga:da")

        lib = Mock()
        lib.replacements = CHAR_REPLACE
        lib.items.return_value = [i]
        lib.albums.return_value = []

        q = Mock()
        a_q = Mock()
        pl = b"$title-my<playlist>.m3u", (q, None), (a_q, None)
        spl._matched_playlists = {pl}

        dir = mkdtemp()
        config["smartplaylist"]["output"] = "extm3u"
        config["smartplaylist"]["prefix"] = "http://beets:8337/files"
        config["smartplaylist"]["relative_to"] = False
        config["smartplaylist"]["playlist_dir"] = str(dir)
        try:
            spl.update_playlists(lib)
        except Exception:
            rmtree(syspath(dir))
            raise

        lib.items.assert_called_once_with(q, None)
        lib.albums.assert_called_once_with(a_q, None)

        m3u_filepath = Path(dir, "ta_ga_da-my_playlist_.m3u")
        assert m3u_filepath.exists()
        content = m3u_filepath.read_bytes()
        rmtree(syspath(dir))

        assert content == (
            b"#EXTM3U\n"
            b"#EXTINF:300,fake artist - fake title\n"
            b"http://beets:8337/files/tagada.mp3\n"
        )

    def test_playlist_update_output_extm3u_fields(self):
        spl = self._make_plugin()

        i = MagicMock()
        type(i).artist = PropertyMock(return_value="Fake Artist")
        type(i).title = PropertyMock(return_value="fake Title")
        type(i).length = PropertyMock(return_value=300.123)
        type(i).path = PropertyMock(return_value=b"/tagada.mp3")
        a = {"id": 456, "genres": ["Rock", "Pop"]}
        i.__getitem__.side_effect = a.__getitem__
        i.evaluate_template.side_effect = lambda pl, *_: os.fsdecode(
            pl
        ).replace("$title", "ta:ga:da")

        lib = Mock()
        lib.replacements = CHAR_REPLACE
        lib.items.return_value = [i]
        lib.albums.return_value = []

        q = Mock()
        a_q = Mock()
        pl = b"$title-my<playlist>.m3u", (q, None), (a_q, None)
        spl._matched_playlists = {pl}

        dir = mkdtemp()
        config["smartplaylist"]["output"] = "extm3u"
        config["smartplaylist"]["relative_to"] = False
        config["smartplaylist"]["playlist_dir"] = str(dir)
        config["smartplaylist"]["fields"] = ["id", "genres"]
        try:
            spl.update_playlists(lib)
        except Exception:
            rmtree(syspath(dir))
            raise

        lib.items.assert_called_once_with(q, None)
        lib.albums.assert_called_once_with(a_q, None)

        m3u_filepath = Path(dir, "ta_ga_da-my_playlist_.m3u")
        assert m3u_filepath.exists()
        content = m3u_filepath.read_bytes()
        rmtree(syspath(dir))

        assert content == (
            b"#EXTM3U\n"
            b'#EXTINF:300 id="456" genres="Rock%3B%20Pop",Fake Artist - fake Title\n'
            b"/tagada.mp3\n"
        )

    def test_get_playlist_items(self):
        """Test get playlist items.

        - Items preserve their order in the playlist
        - There are no duplicates when items match multiple queries
        """
        self.add_item(path=b"/item1.mp3", id=1)
        self.add_item(path=b"/item2.mp3", id=2)
        self.add_item(path=b"/item3.mp3", id=3)
        queries_and_sorts = (("path::item id-", None), ("path::item3", None))

        actual_items = SmartPlaylistPlugin.get_playlist_items(
            self.lib, (queries_and_sorts, None), (None, None)
        )

        assert [i.id for i in actual_items] == [3, 2, 1]


class TestGetItemURI:
    @pytest.fixture
    def plugin_config(self):
        return {}

    @pytest.fixture
    def plugin(self, config, plugin_config):
        plugin_config = {"prefix": "http://beets:8337/files", **plugin_config}
        config["smartplaylist"].set(plugin_config)

        return SmartPlaylistPlugin()

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        monkeypatch.setattr(Item, "destination", lambda _: b"/tagada.mp3")

    @pytest.fixture
    def item(self):
        return item(
            id=3,
            artist="fake artist",
            title="fake title",
            length=300.123,
            path=b"/imported/path/with/dont/move/tagada.mp3",
        )

    @pytest.mark.parametrize(
        "plugin_config, expected_uri",
        [
            _p(
                {},
                b"http://beets:8337/files/imported/path/with/dont/move/tagada.mp3",
                id="path by default",
            ),
            _p(
                {"dest_regen": True},
                b"http://beets:8337/files/tagada.mp3",
                id="dest_regen uses item destination",
            ),
            _p(
                {
                    "uri_format": "http://beets:8337/item/$id/file",
                    "dest_regen": True,
                },
                b"http://beets:8337/item/3/file",
                id="uri_format takes precedence",
            ),
        ],
    )
    def test_get_item_uri(self, plugin, item, expected_uri):
        assert plugin.get_item_uri(item) == expected_uri


class SmartPlaylistCLITest(IOMixin, PluginTestCase):
    plugin = "smartplaylist"

    def setUp(self):
        super().setUp()

        self.item = self.add_item()
        config["smartplaylist"]["playlists"].set(
            [
                {"name": "my_playlist.m3u", "query": self.item.title},
                {"name": "all.m3u", "query": ""},
            ]
        )
        config["smartplaylist"]["playlist_dir"].set(str(self.temp_dir_path))

    def test_splupdate(self):
        with pytest.raises(UserError):
            self.run_with_output("splupdate", "tagada")

        self.run_with_output("splupdate", "my_playlist")
        m3u_path = self.temp_dir_path / "my_playlist.m3u"
        assert m3u_path.exists()
        assert m3u_path.read_bytes() == self.item.path + b"\n"
        os.remove(syspath(m3u_path))

        self.run_with_output("splupdate", "my_playlist.m3u")
        assert m3u_path.read_bytes() == self.item.path + b"\n"
        os.remove(syspath(m3u_path))

        self.run_with_output("splupdate")
        for name in (b"my_playlist.m3u", b"all.m3u"):
            with open(os.path.join(self.temp_dir, name), "rb") as f:
                assert f.read() == self.item.path + b"\n"

    def test_splupdate_unknown_playlist_error_is_sorted_and_quoted(self):
        config["smartplaylist"]["playlists"].set(
            [
                {"name": "z last.m3u", "query": self.item.title},
                {"name": "rock'n roll.m3u", "query": self.item.title},
                {"name": "a one.m3u", "query": self.item.title},
            ]
        )

        with pytest.raises(UserError) as exc_info:
            self.run_with_output("splupdate", "tagada")

        assert str(exc_info.value) == (
            "No playlist matching any of "
            "'a one.m3u' 'rock'\"'\"'n roll.m3u' 'z last.m3u' found"
        )

    def test_splupdate_log_output(self):
        with self.assertLogs("beets.smartplaylist", level="INFO") as logs:
            self.run_with_output("splupdate", "my_playlist")

        output = "\n".join(logs.output)
        assert "Updating 1 smart playlists..." in output
        assert "Creating playlist my_playlist.m3u: 1 tracks." in output
        assert "1 playlists updated" in output

    def test_splupdate_verbose_log_output(self):
        with self.assertLogs("beets.smartplaylist", level="DEBUG") as logs:
            self.run_with_output("splupdate", "my_playlist")

        output = "\n".join(logs.output)
        assert "Updating 1 smart playlists..." in output
        assert "Creating playlist my_playlist.m3u: 1 tracks." in output
        assert "the ärtist - " in output
        assert "1 playlists updated" in output

    def test_splupdate_pretend_log_output(self):
        with self.assertLogs("beets.smartplaylist", level="INFO") as logs:
            self.run_with_output("splupdate", "--pretend", "my_playlist")

        output = "\n".join(logs.output)
        assert "Updating 1 smart playlists..." in output
        assert "Creating playlist my_playlist.m3u: 1 tracks." in output
        assert "1 playlists would be updated" in output


class TestSmartplaylistFileMove(BeetsTestCase):
    """Tests for smartplaylist sync with file moves, metadata changes,
    and interoperability with other db_change subscribers and _memotable.
    """

    @staticmethod
    def _make_plugin():
        spl = SmartPlaylistPlugin()
        spl.register_listener = Mock()
        return spl

    def setUp(self):
        super().setUp()
        config["smartplaylist"]["playlist_dir"].set(str(self.temp_dir_path))
        nones = None, None

        self.pl_beatles = (
            "beatles.m3u",
            parse_query_string("artist:Beatles", Item),
            nones,
        )
        self.pl_rock = (
            "rock.m3u",
            parse_query_string("genres:Rock", Item),
            nones,
        )
        self.pl_album_travel = (
            "travel.m3u",
            nones,
            parse_query_string("for_travel:1", Album),
        )
        self.pl_all_mixed = (
            "mixed.m3u",
            parse_query_string("", Item),
            parse_query_string("", Album),
        )
        self.pl_no_queries = "empty.m3u", nones, nones

    def _populate_plugin(self, spl, playlists):
        spl._unmatched_playlists = set(playlists)
        spl._matched_playlists = set()
        spl._cli_exit_registered = False

    def test_field_level_metadata_change_triggers_update(self):
        """Field-level changes (artist rename) trigger playlist update.

        Covers both:
        - song becomes matching (artist: X -> Beatles) -> playlist needs update
        - song stops matching (artist: Beatles -> Stones) -> playlist needs update
          (the key bug fix: previously only checked if post-change state matched)
        """
        spl = self._make_plugin()
        self._populate_plugin(
            spl, [self.pl_beatles, self.pl_rock, self.pl_no_queries]
        )

        item = self.add_item(artist="Not Beatles", title="Song 1")

        spl.db_change(self.lib, item)
        assert self.pl_beatles in spl._matched_playlists
        assert self.pl_rock in spl._matched_playlists
        assert self.pl_no_queries not in spl._matched_playlists

        spl._unmatched_playlists.update(spl._matched_playlists)
        spl._matched_playlists.clear()

        item.artist = "Beatles"
        item.store()
        spl.db_change(self.lib, item)
        assert self.pl_beatles in spl._matched_playlists

        spl._unmatched_playlists.update(spl._matched_playlists)
        spl._matched_playlists.clear()

        item.artist = "Rolling Stones"
        item.store()
        spl.db_change(self.lib, item)
        assert self.pl_beatles in spl._matched_playlists, (
            "Changing artist FROM Beatles to something else MUST still trigger "
            "playlist update so the song is REMOVED from beatles.m3u"
        )

    def test_file_move_via_item_moved_triggers_sync_path(self):
        """item.move() -> store() -> database_change triggers playlist update.

        Verifies the full path: move_file (with item_moved event) followed by
        store() which emits database_change. Playlists with path-based queries
        must be regenerated.
        """
        spl = self._make_plugin()
        pl_path_based = (
            "pathbased.m3u",
            parse_query_string("path::imported", Item),
            (None, None),
        )
        self._populate_plugin(spl, [pl_path_based, self.pl_beatles])

        item = self.add_item(
            artist="Beatles",
            title="Hey Jude",
            path=b"/imported/Beatles/Hey_Jude.mp3",
        )

        spl.db_change(self.lib, item)
        assert pl_path_based in spl._matched_playlists

        spl._unmatched_playlists.update(spl._matched_playlists)
        spl._matched_playlists.clear()

        item.path = b"/library/Beatles/Hey_Jude.mp3"
        item.store()

        spl.db_change(self.lib, item)
        assert pl_path_based in spl._matched_playlists, (
            "path change via store() (as happens in item.move()) MUST trigger "
            "playlist update for path-dependent queries"
        )
        assert self.pl_beatles in spl._matched_playlists

    def test_album_path_and_artpath_rewrite_sync(self):
        """Album-level changes (artpath, meta) trigger album_query playlists.

        Verifies:
        - Album.store() triggers playlists with album_query
        - Album.store() does NOT mark playlists that have only item queries
          when the item query itself is absent
        - Album.move_art() followed by store triggers sync
        """
        spl = self._make_plugin()
        nones = None, None
        pl_item_only = (
            "itemonly.m3u",
            parse_query_string("artist:Beatles", Item),
            nones,
        )
        self._populate_plugin(
            spl, [self.pl_album_travel, pl_item_only, self.pl_no_queries]
        )

        assert spl._matched_playlists == set()

        album = MagicMock(Album)
        album.albumartist = "Beatles"
        album.album = "Abbey Road"
        album.for_travel = 1
        album.artpath = b"/new/location/cover.jpg"

        spl.db_change(self.lib, album)
        assert self.pl_album_travel in spl._matched_playlists, (
            "Album field change MUST trigger album_query playlist"
        )
        assert pl_item_only not in spl._matched_playlists, (
            "Album change should NOT mark playlists with only item query and "
            "no album_query"
        )
        assert self.pl_no_queries not in spl._matched_playlists

        self._populate_plugin(
            spl, [self.pl_album_travel, pl_item_only, self.pl_no_queries]
        )

        item = MagicMock(Item)
        item.artist = "Beatles"
        item.title = "Come Together"
        spl.db_change(self.lib, item)
        assert pl_item_only in spl._matched_playlists, (
            "Item change SHOULD mark playlists with item_query"
        )
        assert self.pl_album_travel in spl._matched_playlists, (
            "Item change SHOULD also mark playlists with album_query "
            "(items belong to albums)"
        )

    def test_db_change_event_order_smartplaylist_before_other_subscribers(self):
        """Event dispatch order: smartplaylist registered FIRST.

        Other subscribers (e.g. fetchart, web cache invalidation) run after.
        Verifies smartplaylist state is correctly set before downstream plugins
        run, and no cross-contamination occurs.
        """
        call_order = []

        spl = self._make_plugin()
        original_register = spl.register_listener

        def fake_register(event, handler):
            call_order.append(("register", event, handler.__name__))
            return original_register(event, handler)

        spl.register_listener = fake_register
        self._populate_plugin(spl, [self.pl_rock])

        other_plugin_calls = []

        def other_db_change_handler(lib, model):
            call_order.append(("other", "db_change", model))
            other_plugin_calls.append(True)
            if isinstance(model, Item):
                assert spl._cli_exit_registered is True, (
                    "smartplaylist should have already set cli_exit_registered "
                    "when other subscribers see Item db_change (if registered first)"
                )

        BeetsPlugin.listeners.setdefault("database_change", []).append(
            other_db_change_handler
        )
        try:
            pre_call_count = len(other_plugin_calls)
            item = MagicMock(Item)
            item.genres = ["Rock"]
            spl.db_change(self.lib, item)

            assert self.pl_rock in spl._matched_playlists
            assert spl._cli_exit_registered is True
            assert len(other_plugin_calls) == pre_call_count, (
                "Direct call to spl.db_change() should not dispatch to "
                "other listeners via plugins.send()"
            )
        finally:
            BeetsPlugin.listeners["database_change"].remove(
                other_db_change_handler
            )

    def test_db_change_event_order_smartplaylist_after_other_subscribers(self):
        """Event dispatch order: smartplaylist registered LAST.

        e.g. fetchart/web registered before smartplaylist. Verifies that
        previous subscribers mutating state does not break smartplaylist.
        """
        side_effects = []

        def mutating_db_change_handler(lib, model):
            if isinstance(model, Item):
                model["title"] = "Mutated By Other Subscriber"
                side_effects.append(True)

        BeetsPlugin.listeners.setdefault("database_change", []).append(
            mutating_db_change_handler
        )
        try:
            spl = self._make_plugin()
            self._populate_plugin(spl, [self.pl_beatles, self.pl_rock])

            item = MagicMock(Item)
            item.artist = "Beatles"
            item.genres = ["Pop"]
            spl.db_change(self.lib, item)

            assert len(side_effects) == 0, (
                "Direct call to spl.db_change() bypasses plugins.send(); "
                "validates smartplaylist does not trigger other subscribers"
            )
            assert self.pl_beatles in spl._matched_playlists
            assert self.pl_rock in spl._matched_playlists

            spl._unmatched_playlists.update(spl._matched_playlists)
            spl._matched_playlists.clear()

            item.genres = ["Rock", "Pop"]
            spl.db_change(self.lib, item)
            assert self.pl_rock in spl._matched_playlists
        finally:
            BeetsPlugin.listeners["database_change"].remove(
                mutating_db_change_handler
            )

    def test_memotable_invalidation_on_item_remove_coexists_with_sync(self):
        """Item.remove() resets _memotable={} then sends database_change.

        Integration assertion: when Item.remove() is called, it explicitly
        clears `self._db._memotable = {}` BEFORE sending database_change.
        Smartplaylist must survive this and read data correctly.

        This test verifies the event sequence:
          item.remove() -> clears _memotable -> sends database_change
          -> smartplaylist marks playlists -> cli_exit re-queries library
             (with fresh _memotable) -> playlist written correctly.
        """
        spl = self._make_plugin()
        self._populate_plugin(spl, [self.pl_all_mixed])

        item_to_keep = self.add_item(
            artist="Beatles", title="Stay", path=b"/stay.mp3"
        )
        item_to_remove = self.add_item(
            artist="Rolling Stones",
            title="Go Away",
            path=b"/go_away.mp3",
        )

        fake_memokey = ("aunique", "artist", "album", 42)
        self.lib._memotable[fake_memokey] = "cached_value"
        assert fake_memokey in self.lib._memotable

        spl.db_change(self.lib, item_to_remove)
        assert self.pl_all_mixed in spl._matched_playlists
        assert spl._cli_exit_registered is True

        spl._unmatched_playlists.update(spl._matched_playlists)
        spl._matched_playlists.clear()

        self.lib._memotable = {}
        assert fake_memokey not in self.lib._memotable

        spl.db_change(self.lib, item_to_remove)
        assert self.pl_all_mixed in spl._matched_playlists, (
            "smartplaylist must still trigger updates even when _memotable "
            "has just been invalidated (as happens in Item.remove())"
        )

        spl._unmatched_playlists.update(spl._matched_playlists)
        spl._matched_playlists.clear()

        spl._matched_playlists = {self.pl_all_mixed}
        spl.update_playlists(self.lib)
        m3u_path = self.temp_dir_path / "mixed.m3u"
        assert m3u_path.exists()
        content = m3u_path.read_bytes()
        assert item_to_keep.path + b"\n" in content

    def test_memotable_preserved_during_move_does_not_conflict_sync(self):
        """Item.move() does NOT clear _memotable (unlike remove).

        Integration assertion: smartplaylist must neither rely on _memotable
        being cleared, nor must its query execution corrupt _memotable for
        subsequent operations (e.g. web plugin serving concurrent requests).

        Path: item.move() -> store() -> database_change -> smartplaylist marks
              update. _memotable retains values throughout. On cli_exit,
              get_playlist_items() uses fresh queries that still produce
              correct results despite a populated _memotable.
        """
        spl = self._make_plugin()
        self._populate_plugin(spl, [self.pl_beatles])

        beatle = self.add_item(
            artist="Beatles",
            title="Yesterday",
            path=b"/src/Beatles/Yesterday.mp3",
        )
        other = self.add_item(
            artist="Other", title="X", path=b"/src/Other/X.mp3"
        )

        sticky_memokey = ("aunique", "artist", "title", beatle.id)
        self.lib._memotable[sticky_memokey] = "preserved_during_move"

        beatle.path = b"/dst/Beatles/Yesterday.mp3"
        beatle.store()

        spl.db_change(self.lib, beatle)
        assert self.pl_beatles in spl._matched_playlists
        assert sticky_memokey in self.lib._memotable, (
            "_memotable entries should not be cleared by smartplaylist "
            "db_change handler; item.move() semantics preserve them"
        )
        assert (
            self.lib._memotable[sticky_memokey] == "preserved_during_move"
        )

        spl._matched_playlists = {self.pl_beatles}
        spl.update_playlists(self.lib)

        assert sticky_memokey in self.lib._memotable, (
            "update_playlists() should not clear unrelated _memotable keys; "
            "this would break concurrent web-request template evaluation"
        )

        m3u_path = self.temp_dir_path / "beatles.m3u"
        assert m3u_path.exists()
        content = m3u_path.read_bytes()
        assert beatle.path + b"\n" in content
        assert other.path + b"\n" not in content

