Cover Field Cache Invalidation
==============================

The beets core library caches resolved field lookups and related metadata in
``Library._memotable`` to speed up repeated reads during listing, querying,
and template rendering. The caching is designed to be long-lived for the
duration of a ``Library`` instance, which makes *stale data* a problem whenever
cover-art-related fields are written to: any cached references to the old
cover art path or URL would otherwise continue to be served (for example by
the ``web`` plugin) even after the underlying files have changed.

To prevent this, beets provides a declarative extension point so that plugins
which introduce new cover-related fields can ensure that writes to those
fields automatically invalidate the in-memory memotable.


The ``_cover_fields`` Class Variable
------------------------------------

Both :class:`~beets.library.Album` and :class:`~beets.library.Item` inherit
from :class:`~beets.library.LibModel`.  ``LibModel`` defines a class variable
named ``_cover_fields`` – a :class:`set` of field names whose mutation must
trigger an immediate ``_memotable`` flush:

.. code-block:: python

    class LibModel(dbcore.Model["Library"]):
        _cover_fields: ClassVar[set[str]] = set()

        def _setitem(self, key, value):
            changed = super()._setitem(key, value)
            if changed and key in self._cover_fields and self._db:
                self._db._memotable = {}
            return changed

The hook runs on *every* field mutation path: attribute assignment
(``album.artpath = ...``), dictionary-style writes (``item["cover_art_url"]
= ...``), batch :meth:`~beets.dbcore.Model.update`, and any direct call to
``_setitem`` from helper methods such as ``Album.set_art`` / ``move_art``.

The built-in defaults are:

====================  =============================================
Class                 ``_cover_fields``
====================  =============================================
``Album``             ``{"artpath", "cover_art_url"}``
``Item``              ``{"cover_art_url"}``
====================  =============================================


Adding a Custom Cover Field
---------------------------

Suppose you are writing a plugin that stores the URL of a *back* cover on
albums via a flexible field called ``back_cover_url``. To make sure that
changes to ``back_cover_url`` also drop stale memotable entries, simply add
the field name to the corresponding model's ``_cover_fields`` set from your
plugin's setup code.

Recommended template:

.. code-block:: python

    from beets.plugins import BeetsPlugin
    from beets.dbcore import types
    from beets.library import Album, Item


    class BackCoverPlugin(BeetsPlugin):
        """Store and retrieve custom back-cover URLs on albums."""

        album_types = {"back_cover_url": types.STRING}
        item_types = {"per_item_cover_url": types.STRING}

        def __init__(self):
            super().__init__()
            # Register our cover-related fields so that writes invalidate
            # Library._memotable and thus force the web plugin, templates,
            # etc. to see the new value.
            Album._cover_fields.add("back_cover_url")
            Item._cover_fields.add("per_item_cover_url")

Alternatively, if you are subclassing ``Album`` or ``Item`` directly (advanced
use case), override the class variable on your subclass:

.. code-block:: python

    from beets.library import Album


    class MyAlbum(Album):
        """An album variant that tracks an extra front-cover thumbnail."""

        _cover_fields = Album._cover_fields | {"thumbnail_cover_url"}

After registering the field the way shown above, the following operations
will *all* correctly invalidate ``lib._memotable`` for you:

.. code-block:: python

    album.back_cover_url = "https://example.com/back.jpg"   # 1
    album["back_cover_url"] = "https://example.com/back.jpg" # 2
    album.update({"back_cover_url": "..."})                  # 3
    # plus any plugin internal that reaches _setitem.


Best Practices
--------------

* **Register early.**  Add fields to ``_cover_fields`` inside your plugin's
  ``__init__`` or module-level setup code *before* any library operations
  take place. Adding a field to the set after writes have already happened
  will not retroactively clear the memotable.

* **Be specific.**  Only include fields whose values are genuinely used for
  cover-rendering or cover-lookup paths. Over-enrolling fields into
  ``_cover_fields`` causes unnecessary memotable flushes during bulk imports.
  The built-in ``CoverFieldsBenchmarkTest`` verifies that unrelated field
  writes do **not** touch ``_memotable``; keep that property in mind for
  your own fields.

* **Prefer flexible fields.**  ``_cover_fields`` works equally well for
  fixed DB columns (like ``Album.artpath``) and for flexible attributes
  (like ``cover_art_url``). If your plugin ships a new cover attribute,
  declare it via ``album_types`` / ``item_types`` as shown above instead of
  monkey-patching a fixed column.

* **End-to-end verification.**  After wiring a new cover field in, exercise
  the same kind of assertion that ``test_cover_cache.py`` uses for the
  built-in ones: populate ``lib._memotable``, write the field, then assert
  that the memotable dict is immediately empty and that re-reading the
  field yields the freshly-written value.
