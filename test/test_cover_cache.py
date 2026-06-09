"""End-to-end regression tests for cover art cache invalidation.

Tests cover:
1. _memotable clearing when artpath changes via various entry points
2. HTTP response headers (Cache-Control, ETag) for /album/<id>/art
3. ETag changes across file content/replacements including atomic rename
4. End-to-end: set_art then GET /album/<id>/art returns new content
"""

import os
import time
from pathlib import Path
from tempfile import mkstemp
from unittest.mock import patch

import pytest

from beets import util
from beets.library import Album, Item
from beets.test.helper import IOMixin, ItemInDBTestCase
from beetsplug import web


def _write_binary(path: bytes, content: bytes) -> bytes:
    """Write binary content to a file path, creating directories if needed."""
    dir_path = os.path.dirname(path)
    if dir_path and not os.path.isdir(util.syspath(dir_path)):
        os.makedirs(util.syspath(dir_path), exist_ok=True)
    with open(util.syspath(path), "wb") as f:
        f.write(content)
    return path


class MemotableClearingTest(ItemInDBTestCase):
    """Test that _memotable is cleared whenever artpath is modified."""

    def setUp(self):
        super().setUp()
        item = self.add_item_fixture()
        self.album = self.lib.add_album([item])

    def test_memotable_cleared_on_direct_artpath_assignment(self):
        self.lib._memotable = {"cached": "old_value"}
        self.album.artpath = b"/some/new/path.jpg"
        assert self.lib._memotable == {}

    def test_memotable_cleared_on_setitem_artpath(self):
        self.lib._memotable = {"cached": "old_value"}
        self.album["artpath"] = b"/some/new/path.jpg"
        assert self.lib._memotable == {}

    def test_memotable_cleared_on_artpath_set_to_none(self):
        self.album.artpath = b"/old/path.jpg"
        self.lib._memotable = {"cached": "old_value"}
        self.album.artpath = None
        assert self.lib._memotable == {}

    def test_memotable_cleared_on_set_art(self):
        self.config["art_filename"] = "cover"
        old_cover = _write_binary(
            util.bytestring_path(self.temp_dir_path / "old_cover.jpg"),
            b"OLD_IMAGE_BYTES",
        )
        new_cover = _write_binary(
            util.bytestring_path(self.temp_dir_path / "new_cover.jpg"),
            b"NEW_IMAGE_BYTES",
        )

        self.album.set_art(old_cover)
        self.lib._memotable = {"cached": "old_value"}
        self.album.set_art(new_cover)
        assert self.lib._memotable == {}

    def test_memotable_cleared_on_move_art(self):
        self.config["art_filename"] = "cover"
        cover = _write_binary(
            util.bytestring_path(self.temp_dir_path / "cover.jpg"),
            b"SOME_IMAGE",
        )
        self.album.set_art(cover, copy=True)
        assert self.album.artpath is not None

        new_item_dir = self.temp_dir_path / "newdir"
        new_item_dir.mkdir(parents=True, exist_ok=True)
        item = list(self.album.items())[0]
        item.path = util.bytestring_path(str(new_item_dir / "track.mp3"))
        item.store()

        self.lib._memotable = {"cached": "old_value"}
        self.album.move_art()
        assert self.lib._memotable == {}

    def test_memotable_cleared_on_store(self):
        self.lib._memotable = {"cached": "old_value"}
        self.album.album = "New Album Name"
        self.album.store()
        assert self.lib._memotable == {}

    def test_memotable_cleared_on_remove(self):
        self.lib._memotable = {"cached": "old_value"}
        self.album.remove(with_items=False)
        assert self.lib._memotable == {}

    def test_memotable_not_cleared_when_artpath_unchanged(self):
        self.album.artpath = b"/same/path.jpg"
        self.lib._memotable = {"cached": "value"}
        self.album.artpath = b"/same/path.jpg"
        assert self.lib._memotable == {"cached": "value"}


class AlbumArtEndpointCacheHeadersTest(ItemInDBTestCase):
    """Test HTTP headers and ETag behavior for /album/<id>/art."""

    def setUp(self):
        super().setUp()
        item = self.add_item_fixture()
        self.album = self.lib.add_album([item])
        self.config["art_filename"] = "cover"
        cover_bytes = b"FAKE_IMAGE_CONTENT_" + b"\x00" * 100
        self.cover_path = _write_binary(
            util.bytestring_path(self.temp_dir_path / "source_cover.jpg"),
            cover_bytes,
        )
        self.album.set_art(self.cover_path, copy=True)
        self.album.store()

        web.app.config["TESTING"] = True
        web.app.config["lib"] = self.lib
        self.client = web.app.test_client()

    def _req(self, headers=None):
        return self.client.get(f"/album/{self.album.id}/art", headers=headers or {})

    def test_album_art_returns_200_and_content(self):
        resp = self._req()
        assert resp.status_code == 200
        with open(util.syspath(self.album.artpath), "rb") as f:
            assert resp.data == f.read()

    def test_album_art_returns_404_when_no_art(self):
        self.album.artpath = None
        self.album.store()
        resp = self.client.get(f"/album/{self.album.id}/art")
        assert resp.status_code == 404

    def test_cache_control_headers_disable_browser_caching(self):
        resp = self._req()
        cc = resp.headers.get("Cache-Control", "")
        assert "no-cache" in cc
        assert "no-store" in cc
        assert "must-revalidate" in cc
        assert resp.headers.get("Pragma") == "no-cache"
        assert resp.headers.get("Expires") == "0"

    def test_etag_contains_mtime_size_ino(self):
        resp = self._req()
        etag = resp.headers.get("ETag")
        assert etag is not None
        stat = os.stat(util.syspath(self.album.artpath))
        parts = etag.strip('"').split("-")
        assert parts[0] == str(self.album.id)
        assert str(stat.st_mtime_ns) in parts
        assert str(stat.st_ino) in parts
        assert str(stat.st_size) in parts

    def test_etag_changes_after_file_content_replaced(self):
        resp1 = self._req()
        etag1 = resp1.headers.get("ETag")

        time.sleep(0.01)
        with open(util.syspath(self.album.artpath), "wb") as f:
            f.write(b"COMPLETELY_DIFFERENT_BYTES_" + b"\xff" * 200)
        os.utime(util.syspath(self.album.artpath), None)

        resp2 = self._req()
        etag2 = resp2.headers.get("ETag")
        assert etag1 != etag2

    def test_etag_changes_after_atomic_rename(self):
        resp1 = self._req()
        etag1 = resp1.headers.get("ETag")

        fd, tmp_path = mkstemp(dir=str(self.temp_dir_path), suffix=".jpg")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(b"ATOMIC_REPLACEMENT_BYTES_" + b"\xee" * 150)
            orig_stat = os.stat(util.syspath(self.album.artpath))
            try:
                os.utime(tmp_path, (orig_stat.st_atime, orig_stat.st_mtime))
            except OSError:
                pass
            os.replace(tmp_path, util.syspath(self.album.artpath))
        except Exception:
            os.unlink(tmp_path)
            raise

        resp2 = self._req()
        etag2 = resp2.headers.get("ETag")
        assert etag1 != etag2

    def test_if_none_match_with_same_etag_returns_200_with_same_content(self):
        """With Cache-Control: no-store, server should return 200 even with
        matching ETag, since we explicitly disable conditional caching.
        This ensures the browser always gets the latest data from server.
        """
        resp1 = self._req()
        etag1 = resp1.headers.get("ETag")
        resp2 = self._req(
            headers={"If-None-Match": etag1}
        )
        assert resp2.status_code == 200
        assert resp2.data == resp1.data


class SetArtEndToEndTest(ItemInDBTestCase):
    """End-to-end: set_art updates the content returned by GET /album/<id>/art."""

    def setUp(self):
        super().setUp()
        item = self.add_item_fixture()
        self.album = self.lib.add_album([item])
        self.config["art_filename"] = "cover"

        web.app.config["TESTING"] = True
        web.app.config["lib"] = self.lib
        self.client = web.app.test_client()

    def test_set_art_then_get_returns_new_image_bytes(self):
        old_bytes = b"OLD_COVER_IMAGE_DATA_v1" + b"\x11" * 50
        new_bytes = b"NEW_COVER_IMAGE_DATA_v2" + b"\x22" * 80

        old_cover = _write_binary(
            util.bytestring_path(self.temp_dir_path / "old_cover.jpg"),
            old_bytes,
        )
        new_cover = _write_binary(
            util.bytestring_path(self.temp_dir_path / "new_cover.jpg"),
            new_bytes,
        )

        self.album.set_art(old_cover)
        self.album.store()
        resp1 = self.client.get(f"/album/{self.album.id}/art")
        assert resp1.status_code == 200
        assert resp1.data == old_bytes

        self.album.set_art(new_cover)
        self.album.store()
        resp2 = self.client.get(f"/album/{self.album.id}/art")
        assert resp2.status_code == 200
        assert resp2.data == new_bytes
        assert resp2.data != resp1.data

    def test_set_art_different_sizes_reflects_in_response(self):
        small = b"SMALL" * 10
        big = b"BIG" * 1000
        small_path = _write_binary(
            util.bytestring_path(self.temp_dir_path / "small.jpg"),
            small,
        )
        big_path = _write_binary(
            util.bytestring_path(self.temp_dir_path / "big.jpg"),
            big,
        )

        self.album.set_art(small_path)
        self.album.store()
        r_small = self.client.get(f"/album/{self.album.id}/art")
        assert len(r_small.data) == len(small)

        self.album.set_art(big_path)
        self.album.store()
        r_big = self.client.get(f"/album/{self.album.id}/art")
        assert len(r_big.data) == len(big)
        assert r_big.data == big

    def test_cover_replaced_via_rename_returns_new_content(self):
        orig_bytes = b"ORIGINAL_COVER_V1" + b"\xaa" * 50
        new_bytes = b"REPLACED_COVER_V2" + b"\xbb" * 200

        cover = _write_binary(
            util.bytestring_path(self.temp_dir_path / "orig_source.jpg"),
            orig_bytes,
        )
        self.album.set_art(cover, copy=True)
        self.album.store()

        resp_orig = self.client.get(f"/album/{self.album.id}/art")
        assert resp_orig.data == orig_bytes

        fd, tmp = mkstemp(dir=str(self.temp_dir_path), suffix=".jpg")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(new_bytes)
            os.replace(tmp, util.syspath(self.album.artpath))
        except Exception:
            os.unlink(tmp)
            raise

        resp_new = self.client.get(f"/album/{self.album.id}/art")
        assert resp_new.status_code == 200
        assert resp_new.data == new_bytes

    def test_clear_art_returns_404(self):
        cover = _write_binary(
            util.bytestring_path(self.temp_dir_path / "cover.jpg"),
            b"COVER",
        )
        self.album.set_art(cover)
        self.album.store()
        assert self.client.get(f"/album/{self.album.id}/art").status_code == 200

        self.album.artpath = None
        self.album.store()
        assert self.client.get(f"/album/{self.album.id}/art").status_code == 404

