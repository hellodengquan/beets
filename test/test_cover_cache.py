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

    def test_memotable_cleared_on_album_cover_art_url_change(self):
        self.lib._memotable = {"cached": "old_value"}
        self.album.cover_art_url = "http://example.com/cover.jpg"
        assert self.lib._memotable == {}

    def test_memotable_cleared_on_album_cover_art_url_set_to_none(self):
        self.album.cover_art_url = "http://example.com/old.jpg"
        self.lib._memotable = {"cached": "old_value"}
        self.album.cover_art_url = None
        assert self.lib._memotable == {}

    def test_memotable_not_cleared_when_album_cover_art_url_unchanged(self):
        self.album.cover_art_url = "http://example.com/same.jpg"
        self.lib._memotable = {"cached": "value"}
        self.album.cover_art_url = "http://example.com/same.jpg"
        assert self.lib._memotable == {"cached": "value"}


class ItemMemotableClearingTest(ItemInDBTestCase):
    """Test that _memotable is cleared when Item.cover_art_url is modified."""

    def setUp(self):
        super().setUp()
        self.item = self.add_item_fixture()

    def test_memotable_cleared_on_item_cover_art_url_assignment(self):
        self.lib._memotable = {"cached": "old_value"}
        self.item.cover_art_url = "http://example.com/track_cover.jpg"
        assert self.lib._memotable == {}

    def test_memotable_cleared_on_item_cover_art_url_setitem(self):
        self.lib._memotable = {"cached": "old_value"}
        self.item["cover_art_url"] = "http://example.com/track_cover.jpg"
        assert self.lib._memotable == {}

    def test_memotable_cleared_on_item_cover_art_url_set_to_none(self):
        self.item.cover_art_url = "http://example.com/old.jpg"
        self.lib._memotable = {"cached": "old_value"}
        self.item.cover_art_url = None
        assert self.lib._memotable == {}

    def test_memotable_not_cleared_when_item_cover_art_url_unchanged(self):
        self.item.cover_art_url = "http://example.com/same.jpg"
        self.lib._memotable = {"cached": "value"}
        self.item.cover_art_url = "http://example.com/same.jpg"
        assert self.lib._memotable == {"cached": "value"}

    def test_memotable_not_cleared_on_item_unrelated_field(self):
        self.lib._memotable = {"cached": "value"}
        self.item.title = "New Title"
        assert self.lib._memotable == {"cached": "value"}

    def test_memotable_cleared_on_item_store(self):
        self.lib._memotable = {"cached": "old_value"}
        self.item.title = "New Title"
        self.item.store()
        assert self.lib._memotable == {}

    def test_memotable_cleared_on_item_remove(self):
        self.lib._memotable = {"cached": "old_value"}
        self.item.remove()
        assert self.lib._memotable == {}


class CoverFieldsBenchmarkTest(ItemInDBTestCase):
    """Benchmark-style assertions to quantify the overhead of the
    _setitem interceptor under bulk write scenarios.

    These tests assert relative order-of-magnitude bounds rather than
    absolute wall-clock times, so they remain stable on CI hardware.
    """

    def setUp(self):
        super().setUp()
        item = self.add_item_fixture()
        self.album = self.lib.add_album([item])
        self.item = item

    def test_bulk_album_non_cover_writes_clear_count_is_zero(self):
        """10k writes to unrelated album fields must NEVER clear _memotable.

        This is a correctness guard, not a timing benchmark: the interceptor
        must only trigger for fields declared in _cover_fields.
        """
        self.lib._memotable = {"marker": True}
        for i in range(10_000):
            self.album.title = f"Title_{i}"
        assert "marker" in self.lib._memotable, (
            "Writing title (not in _cover_fields) must not clear _memotable"
        )

    def test_bulk_album_cover_writes_clear_count_is_10k(self):
        """10k writes to album.artpath must clear _memotable exactly 10k times
        (once per actual change), proving the interceptor triggers correctly."""
        clears = 0
        for i in range(10_000):
            self.lib._memotable = {"seq": i}
            self.album.artpath = f"/path/{i}.jpg".encode()
            if self.lib._memotable == {}:
                clears += 1
        assert clears == 10_000, (
            f"Expected 10000 memotable clears but got {clears}"
        )

    def test_interceptor_overhead_non_cover_writes_under_2x(self):
        """The _setitem interceptor must not more than double the cost of
        10k writes to fields that are NOT in _cover_fields.

        We compare: baseline (raw dbcore _setitem via patch) vs intercepted.
        A 2x safety margin should hold on all reasonable hardware.
        """
        import timeit

        N = 10_000

        dbcore_setitem = super(type(self.album), type(self.album))._setitem

        def run_with_patch():
            original = type(self.album)._setitem
            try:
                type(self.album)._setitem = dbcore_setitem
                for i in range(N):
                    self.album.title = f"T{i}"
                    self.album.artist = f"A{i}"
                    self.album.year = 2000 + (i % 30)
                    self.album.tracktotal = 10
            finally:
                type(self.album)._setitem = original

        def run_with_interceptor():
            for i in range(N):
                self.album.title = f"T{i}"
                self.album.artist = f"A{i}"
                self.album.year = 2000 + (i % 30)
                self.album.tracktotal = 10

        baseline = timeit.timeit(run_with_patch, number=1)
        intercepted = timeit.timeit(run_with_interceptor, number=1)

        ratio = intercepted / max(baseline, 1e-9)
        assert ratio < 2.0, (
            f"_setitem interceptor overhead too high: "
            f"baseline={baseline:.4f}s intercepted={intercepted:.4f}s ratio={ratio:.2f}x"
        )

    def test_bulk_item_non_cover_writes_no_memotable_clear(self):
        """10k writes to unrelated Item fields must NEVER clear _memotable."""
        self.lib._memotable = {"item_marker": True}
        for i in range(10_000):
            self.item.title = f"Track_{i}"
            self.item.artist = f"Artist_{i % 100}"
        assert "item_marker" in self.lib._memotable, (
            "Writing title/artist (not in _cover_fields) must not clear _memotable"
        )

    def test_cover_fields_classvars_are_configured(self):
        """Sanity-check: subclasses must declare the right _cover_fields."""
        assert Album._cover_fields == frozenset({"artpath", "cover_art_url"})
        assert isinstance(Album._cover_fields, frozenset)
        assert Item._cover_fields == frozenset({"cover_art_url"})
        assert isinstance(Item._cover_fields, frozenset)
        from beets.library.models import LibModel
        assert LibModel._cover_fields == frozenset()
        assert isinstance(LibModel._cover_fields, frozenset)


class RegisterCoverFieldTest(ItemInDBTestCase):
    """Exercise the public :func:`register_cover_field` helper: basic
    semantics, argument validation, idempotence, thread-safety and
    live end-to-end proof that a freshly-registered flexible field
    actually triggers ``_memotable`` invalidation on write.
    """

    def setUp(self):
        super().setUp()
        item = self.add_item_fixture()
        self.album = self.lib.add_album([item])
        self.item = item
        # Snapshot the built-in defaults so each test can undo its
        # registrations without leaking side effects into other tests.
        self._orig_album_fields = Album._cover_fields
        self._orig_item_fields = Item._cover_fields

    def tearDown(self):
        # Restore the pristine built-in cover field sets.  Because the
        # class attribute is replaced with a fresh frozenset during each
        # registration, a plain assignment back to the captured reference
        # is enough to reset state.
        Album._cover_fields = self._orig_album_fields
        Item._cover_fields = self._orig_item_fields
        super().tearDown()

    # ------------------------------------------------------------------
    # Basic semantics
    # ------------------------------------------------------------------
    def test_registered_field_writes_clear_memotable(self):
        """After register_cover_field(Album, 'back_cover_url'), writes
        to that flexible field must flush _memotable."""
        from beets.library import register_cover_field

        register_cover_field(Album, "back_cover_url")
        assert "back_cover_url" in Album._cover_fields

        self.lib._memotable = {"stale": "entry"}
        self.album.back_cover_url = "http://example.com/back.jpg"
        assert self.lib._memotable == {}

    def test_registered_item_field_writes_clear_memotable(self):
        from beets.library import register_cover_field

        register_cover_field(Item, "digipak_cover_url")
        assert "digipak_cover_url" in Item._cover_fields

        self.lib._memotable = {"stale": "entry"}
        self.item.digipak_cover_url = "http://example.com/digipak.jpg"
        assert self.lib._memotable == {}

    def test_register_is_idempotent(self):
        """Registering the same (cls, name) twice is a no-op."""
        from beets.library import register_cover_field

        before = Album._cover_fields
        register_cover_field(Album, "artpath")  # already built in
        register_cover_field(Album, "artpath")
        register_cover_field(Album, "artpath")
        assert Album._cover_fields == before

    # ------------------------------------------------------------------
    # Argument validation
    # ------------------------------------------------------------------
    def test_rejects_empty_field_name(self):
        from beets.library import register_cover_field

        with pytest.raises(ValueError):
            register_cover_field(Album, "")

    def test_rejects_non_string_field_name(self):
        from beets.library import register_cover_field

        with pytest.raises(ValueError):
            register_cover_field(Album, None)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            register_cover_field(Album, 123)  # type: ignore[arg-type]

    def test_rejects_non_libmodel_subclass(self):
        from beets.library import register_cover_field

        class NotALibModel:
            pass

        with pytest.raises(TypeError):
            register_cover_field(NotALibModel, "foo")  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            register_cover_field("not a class", "foo")  # type: ignore[arg-type]

    def test_importable_from_beets_library_namespace(self):
        """register_cover_field must be exposed as a public API under
        ``beets.library`` (docs point users to this import)."""
        from beets.library import register_cover_field as from_lib
        from beets.library.models import register_cover_field as from_models

        assert from_lib is from_models

    # ------------------------------------------------------------------
    # Concurrent registration
    # ------------------------------------------------------------------
    def test_concurrent_registers_all_fields_present(self):
        """N worker threads each register a disjoint set of field names.
        At the end, every proposed name must appear in Album._cover_fields.

        This catches races where a ``frozenset | {name}`` rebuild is lost
        because two threads read the old value concurrently and one of
        the assignments overwrites the other.
        """
        import threading
        from beets.library import register_cover_field

        NUM_THREADS = 12
        FIELDS_PER_THREAD = 50
        errors: list[Exception] = []

        def worker(tid: int):
            try:
                for i in range(FIELDS_PER_THREAD):
                    name = f"thread_{tid}_cover_field_{i}"
                    register_cover_field(Album, name)
            except Exception as exc:  # pragma: no cover - best-effort
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(NUM_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"threads raised: {errors!r}"

        expected = {
            f"thread_{t}_cover_field_{i}"
            for t in range(NUM_THREADS)
            for i in range(FIELDS_PER_THREAD)
        }
        missing = expected - set(Album._cover_fields)
        assert not missing, (
            f"Concurrent register_cover_field missed {len(missing)} fields: "
            f"{sorted(missing)[:10]}..."
        )
        assert len(Album._cover_fields) == len(self._orig_album_fields) + len(expected)

    def test_concurrent_registers_interleaved_with_writes(self):
        """Concurrent registrations must not break the interceptor.

        Thread A keeps writing fields (some already registered, some
        not); Threads B/C keep registering new fields.  Thread A must
        observe that any write it performs to a field *after* that
        field's registration has been observed by Thread A consistently
        clears the memotable.  Weaker assertion: no exceptions occur
        and the final class attribute is still a valid frozenset.
        """
        import threading
        import random
        from beets.library import register_cover_field

        errors: list[Exception] = []
        stop = threading.Event()

        def writer():
            try:
                counter = 0
                while not stop.is_set():
                    # Alternate between a definitely-built-in field, a
                    # definitely-not-registered-yet field, and an
                    # unrelated field.
                    pick = counter % 3
                    if pick == 0:
                        self.album.cover_art_url = f"http://ex.com/u{counter}"
                    elif pick == 1:
                        self.album[f"custom_field_{counter}"] = "x"
                    else:
                        self.album.title = f"t{counter}"
                    counter += 1
            except Exception as exc:  # pragma: no cover - best-effort
                errors.append(exc)

        def registrations(prefix: str):
            try:
                for i in range(80):
                    register_cover_field(Album, f"{prefix}_{i}")
            except Exception as exc:  # pragma: no cover - best-effort
                errors.append(exc)

        threads: list[threading.Thread] = [threading.Thread(target=writer)]
        for p in ("a", "b", "c"):
            threads.append(threading.Thread(target=registrations, args=(p,)))

        for t in threads:
            t.start()
        # Let them overlap briefly.
        for t in threads[1:]:
            t.join()
        stop.set()
        threads[0].join()

        assert errors == [], f"threads raised: {errors!r}"
        assert isinstance(Album._cover_fields, frozenset)
        # Every explicitly-registered name must be observed.
        for p in ("a", "b", "c"):
            for i in range(80):
                assert f"{p}_{i}" in Album._cover_fields


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


class PluginIntegrationMemotableTest(ItemInDBTestCase):
    """End-to-end tests that exercise the actual code paths of the
    built-in ``fetchart`` and ``embedart`` plugins, verifying that
    ``_memotable`` is correctly invalidated when each plugin writes
    cover-related fields.

    These tests catch the downstream integration blind spot: the core
    _setitem interceptor can be correct in isolation, but if a plugin
    ever bypasses it (e.g. via monkey-patching or writing to the raw
    DB row) the bug would otherwise not show up in a unit test.
    """

    def setUp(self):
        super().setUp()
        item = self.add_item_fixture()
        self.album = self.lib.add_album([item])
        self.config["art_filename"] = "cover"
        # Album.set_art emits an ``art_set`` signal, to which
        # EmbedCoverArtPlugin listens via process_album. The listener
        # reads self.config["auto"], which normally gets its default
        # value when the plugin is instantiated. In these tests we
        # don't always construct a full plugin, so we seed the
        # confuse tree with the embedart defaults up-front.
        self.config["embedart"].set(
            {
                "maxwidth": 0,
                "auto": True,
                "compare_threshold": 0,
                "ifempty": False,
                "remove_art_file": False,
                "quality": 0,
                "clearart_on_import": False,
            }
        )

    # ------------------------------------------------------------------
    # fetchart integration
    # ------------------------------------------------------------------
    def test_fetchart_set_art_clears_memotable(self):
        """FetchArtPlugin._set_art(album, candidate) must clear _memotable.

        This is the exact code path used by both the ``fetchart`` CLI
        command and the post-import ``assign_art`` hook.

        We do **not** construct a full FetchArtPlugin instance here because
        that requires the entire confuse configuration tree for unrelated
        plugins (e.g. ``embedart.auto``) to be populated, which is outside
        the scope of a cache-invalidation regression test.  Instead we
        invoke ``_set_art`` with a minimal fake plugin whose attributes
        mirror exactly what the real method reads (``_log`` and
        ``store_source``).  The implementation of ``_set_art`` delegates
        straight to ``album.set_art`` – that is the code under test.
        """
        from beetsplug.fetchart import Candidate, FetchArtPlugin, MetadataMatch
        import logging
        from types import SimpleNamespace

        cover_bytes = b"FETCHART_INTEGRATION_TEST_CONTENT"
        local_cover = _write_binary(
            util.bytestring_path(self.temp_dir_path / "fetched_cover.jpg"),
            cover_bytes,
        )

        candidate = Candidate(
            log=logging.getLogger("fetchart.test"),
            source_name="filesystem",
            path=local_cover,
            match=MetadataMatch.EXACT,
        )
        fake_plugin = SimpleNamespace(
            _log=logging.getLogger("fetchart.test"),
            store_source=False,
        )

        self.lib._memotable = {"stale": "cache_entry"}
        ok = FetchArtPlugin._set_art(fake_plugin, self.album, candidate, delete=False)
        assert ok is True
        assert self.lib._memotable == {}, (
            "FetchArtPlugin._set_art must invalidate _memotable via "
            "the Album.set_art → _setitem interceptor chain."
        )
        assert self.album.artpath is not None
        with open(util.syspath(self.album.artpath), "rb") as f:
            assert f.read() == cover_bytes

    def test_fetchart_art_source_flex_field_does_not_clear_twice(self):
        """Assigning album.art_source (not in _cover_fields) during
        fetchart's post-set_art step must not trigger a redundant flush.

        The actual memotable-clearing happens inside _set_art → set_art.
        We verify that writing art_source afterwards keeps it empty
        (no extra empty-dict assignment is needed, but the point is that
        unrelated fields must not raise).
        """
        from beetsplug.fetchart import Candidate, FetchArtPlugin, MetadataMatch
        import logging
        from types import SimpleNamespace

        local_cover = _write_binary(
            util.bytestring_path(self.temp_dir_path / "a.jpg"),
            b"CONTENT",
        )
        candidate = Candidate(
            log=logging.getLogger("fetchart.test"),
            source_name="filesystem",
            path=local_cover,
            match=MetadataMatch.EXACT,
        )
        fake_plugin = SimpleNamespace(
            _log=logging.getLogger("fetchart.test"),
            store_source=True,
        )

        self.lib._memotable = {"stale": "cache_entry"}
        ok = FetchArtPlugin._set_art(fake_plugin, self.album, candidate, delete=False)
        assert ok is True
        assert self.album.art_source == "filesystem"  # set by plugin
        assert self.lib._memotable == {}

    # ------------------------------------------------------------------
    # embedart integration
    # ------------------------------------------------------------------
    def test_embedart_remove_artfile_clears_memotable(self):
        """EmbedCoverArtPlugin.remove_artfile sets artpath = None → memotable flush.

        This is the exact code path used when ``embedart`` is configured
        with ``remove_art_file: yes`` and after an ``embed``/``import``.
        """
        from beetsplug.embedart import EmbedCoverArtPlugin

        cover = _write_binary(
            util.bytestring_path(self.temp_dir_path / "to_remove_cover.jpg"),
            b"EMBED_TO_REMOVE",
        )
        self.album.set_art(cover, copy=True)
        self.album.store()
        assert self.album.artpath is not None
        assert os.path.isfile(util.syspath(self.album.artpath))

        plugin = EmbedCoverArtPlugin()
        plugin.config["remove_art_file"] = True

        self.lib._memotable = {"stale": "cache_entry"}
        plugin.remove_artfile(self.album)

        assert self.album.artpath is None, (
            "remove_artfile must set artpath to None when file is removed"
        )
        assert not os.path.isfile(util.syspath(self.album.artpath or b"")), (
            "remove_artfile must actually delete the art file on disk"
        )
        assert self.lib._memotable == {}, (
            "EmbedArtPlugin.remove_artfile sets album.artpath = None, "
            "which must go through the _setitem interceptor and flush "
            "_memotable."
        )

    def test_embedart_extractart_associate_clears_memotable(self):
        """Simulating extractart -o <path> --associate: art.extract_first
        writes a file, then album.set_art associates it → memotable flush.

        We cannot call extract_func directly because it drives the CLI
        argument parser; instead we call the same primitives in order:
        produce a cover file on disk (here we pre-create one instead of
        relying on MediaFile embedded images), then call album.set_art()
        and album.store(), mirroring exactly what the plugin code does
        in the opts.associate branch.
        """
        cover_bytes = b"EXTRACTED_EMBEDDED_IMAGE_FROM_MEDIAFILE"
        extracted_artpath = _write_binary(
            util.bytestring_path(
                os.path.join(
                    util.syspath(self.album.path), "extracted.jpg"
                )
            ),
            cover_bytes,
        )

        self.lib._memotable = {"stale": "before_extract"}
        # Replicate the body of embedart.extract_func's associate branch:
        self.album.set_art(extracted_artpath)
        self.album.store()

        assert self.lib._memotable == {}, (
            "extractart --associate calls album.set_art + album.store(); "
            "the set_art → _setitem chain must flush _memotable."
        )
        assert self.album.artpath is not None
        with open(util.syspath(self.album.artpath), "rb") as f:
            assert f.read() == cover_bytes

    # ------------------------------------------------------------------
    # clearart (via embedart module-level helper) integration
    # ------------------------------------------------------------------
    def test_embedart_clearart_sets_artpath_none_on_album(self):
        """After art.clear() removes embedded images the embedart plugin
        leaves the external album art alone; but when the user manually
        nulls out the album's artpath afterwards, it must still flush.

        This is a defensive regression test. ``art.clear()`` operates on
        MediaFile embedded images and does not touch ``album.artpath``,
        so users typically follow it up with ``album.artpath = None;
        album.store()``.  The interceptor must fire for that manual step.
        """
        cover = _write_binary(
            util.bytestring_path(self.temp_dir_path / "x.jpg"),
            b"X",
        )
        self.album.set_art(cover, copy=True)
        self.album.store()

        self.lib._memotable = {"stale": "entry"}
        # User invokes `beet clearart` then manually removes external art.
        self.album.artpath = None
        self.album.store()

        assert self.album.artpath is None
        assert self.lib._memotable == {}


