.. _query-normalization-api:

Query Normalization API
======================

.. currentmodule:: beets.library.queries

This page documents the *query normalization pipeline* introduced in beets
2.12. Every plugin that constructs queries from user input or from
configuration should migrate to this new public API. It guarantees:

- consistent handling of query prefixes (``:`` for regex, ``=`` for exact
  matching, ``=~`` for string matching) and any prefixes contributed by other
  plugins via :func:`beets.plugins.queries`;
- automatic detection of *implicit path queries*, so that a file path on the
  command line is correctly translated to a ``path:<path>`` lookup;
- uniform error translation so that malformed numeric / date / regex input is
  wrapped in :class:`~beets.dbcore.InvalidQueryError` for the UI layer to
  display;
- a single, well-tested code path so you don't need to repeat the four steps
  shown above in every plugin.

.. seealso::

    :ref:`The beets query syntax reference <queries>` documents the
    user-facing language that this API parses.


Why Migrate
-----------

Plugins written against the legacy low-level helpers under
:mod:`beets.dbcore.queryparse` usually miss at least one of the bullet points
above. For example, before this migration the ``advancedrewrite`` plugin
called :func:`~beets.dbcore.query_from_strings` with an empty ``prefixes={}``
dictionary, which silently disabled the ``artist::regex`` / ``genre:=exact``
syntax for users of that plugin — a classic symptom of the duplication this
API is designed to eliminate.

The new public API lives inside :mod:`beets.library.queries` and is
re-exported from :mod:`beets.library` for convenient importing.


Quick Import
------------

In the vast majority of cases you only need to import one of the following
two names:

.. code-block:: python

    from beets.library import QueryNormalizationContext, build_query_context


Overview of the Pipeline
------------------------

Every ``QueryNormalizationContext`` encapsulates three pieces of configuration
that used to be repeated by every caller:

1. **Query prefixes** — a dict mapping tokens like ``:``, ``=`` and ``=~`` to
   their :class:`~beets.dbcore.query.FieldQuery` subclasses, merged with any
   additional prefixes provided by loaded plugins.
2. **Case-insensitive sort toggle** — the value of
   ``config["sort_case_insensitive"]`` at the time the context was built.
3. **Normalization toggle** — whether :func:`normalize_query_parts` should be
   applied to each query-part list before parsing.

The context then exposes four higher-level *builder* methods that combine
these pieces with the actual parsing logic.


.. _querynorm-context-api:

The ``QueryNormalizationContext`` Class
--------------------------------------

.. autoclass:: beets.library.queries.QueryNormalizationContext
   :members:
   :undoc-members:

Constructor fields
~~~~~~~~~~~~~~~~~~

Instances are normally produced by :func:`build_query_context`, but the
dataclass fields are public and may be useful for introspection:

.. attribute:: prefixes
   :type: dict[str, type[beets.dbcore.query.FieldQuery]]

   The effective query-prefix map. Merges the built-in prefixes (``:``,
   ``=``, ``=~``) with those registered through :func:`beets.plugins.queries`.

.. attribute:: case_insensitive
   :type: bool

   Whether sort operations should treat strings as case-insensitive. Mirrors
   the ``sort_case_insensitive`` configuration option.

.. attribute:: apply_parts_normalization
   :type: bool

   When ``True`` (the default), :meth:`parse_sorted`, :meth:`build_collection`
   and :meth:`parse_string` will run incoming parts through
   :func:`normalize_query_parts` first.


Helper Functions
----------------

.. autofunction:: beets.library.queries.get_query_prefixes
.. autofunction:: beets.library.queries.get_sort_case_insensitive
.. autofunction:: beets.library.queries.normalize_query_parts
.. autofunction:: beets.library.queries.build_query_context
.. autofunction:: beets.library.queries.parse_query_parts
.. autofunction:: beets.library.queries.parse_query_string


.. _querynorm-migration:

Migration from Legacy APIs
==========================

The table below summarises which legacy helpers should be replaced by which
new context methods. Following sections give concrete side-by-side examples.

+---------------------------------------------+--------------------------------------------------+
| Legacy API                                  | Replacement in ``QueryNormalizationContext``     |
+=============================================+==================================================+
| ``dbcore.query_from_strings(...)``          | :meth:`~QueryNormalizationContext.build_collection` |
+---------------------------------------------+--------------------------------------------------+
| ``dbcore.parse_sorted_query(...)``          | :meth:`~QueryNormalizationContext.parse_sorted`  |
+---------------------------------------------+--------------------------------------------------+
| manual ``shlex.split`` + *parse_* call      | :meth:`~QueryNormalizationContext.parse_string`  |
+---------------------------------------------+--------------------------------------------------+
| ad-hoc ``if isinstance(query, str)`` / ...  | :meth:`~QueryNormalizationContext.parse`         |
+---------------------------------------------+--------------------------------------------------+


Example 1 – Replacing ``query_from_strings``
--------------------------------------------

The ``advancedrewrite`` plugin used to build a boolean "match" query from a
user-supplied pattern like this:

.. code-block:: python
    :caption: Before – low-level, inconsistent

    import shlex
    from beets.dbcore import AndQuery, query_from_strings
    from beets.library import Item

    def match_item(self, rule: dict) -> None:
        pattern = rule["match"]
        query = query_from_strings(
            AndQuery,
            Item,
            prefixes={},                       # ← all prefixes disabled!
            query_parts=shlex.split(pattern),  # ← no path normalization
        )

This suffers from three defects:

1. Prefixes are empty → the user can't use ``field::regex`` syntax.
2. :func:`~beets.dbcore.query.PathQuery.is_path_query` is never called →
   an existing file path in the pattern won't be matched as a path.
3. :class:`~beets.dbcore.query.InvalidQueryArgumentValueError` (for example
   from a bad numeric field) bubbles up raw, bypassing the UI error handler.

The migrated version reads:

.. code-block:: python
    :caption: After – through the normalization pipeline

    import shlex
    from beets.dbcore import AndQuery
    from beets.library import Item, build_query_context

    def match_item(self, rule: dict) -> None:
        pattern = rule["match"]
        ctx = build_query_context()
        query = ctx.build_collection(
            AndQuery,
            shlex.split(pattern),
            Item,
        )

And now the three defects are all gone:

* ``ctx.prefixes`` already contains ``:``, ``=``, ``=~`` and every
  plugin-contributed prefix.
* The part list is fed through :func:`normalize_query_parts` before parsing,
  so paths are converted to ``path:<path>`` automatically.
* Any ``InvalidQueryArgumentValueError`` is caught and re-raised as
  :class:`InvalidQueryError` with the original parts attached.


Example 2 – Replacing ``parse_sorted_query``
--------------------------------------------

``smartplaylist`` and similar plugins need to retrieve a ``(query, sort)``
pair from a configuration string. The legacy approach was to call
``dbcore.parse_sorted_query`` directly and re-build the prefix map each time:

.. code-block:: python
    :caption: Before – repeated prefix construction

    import beets.config
    from beets import plugins
    from beets.dbcore import parse_sorted_query, query
    from beets.library import Album

    def compile_rule(rule_query: str):
        prefixes = {
            ":": query.RegexpQuery,
            "=~": query.StringQuery,
            "=": query.MatchQuery,
        }
        prefixes.update(plugins.queries())
        case_insensitive = beets.config["sort_case_insensitive"].get(bool)
        q, sort = parse_sorted_query(
            Album,
            shlex.split(rule_query),
            prefixes,
            case_insensitive,
        )
        try:
            ...
        except query.InvalidQueryArgumentValueError as exc:
            ...

Switching to the new pipeline removes all of the boilerplate:

.. code-block:: python
    :caption: After – one context covers everything

    from beets.library import Album, build_query_context

    def compile_rule(rule_query: str):
        ctx = build_query_context()
        q, sort = ctx.parse_string(rule_query, Album)
        # InvalidQueryArgumentValueError is already wrapped in InvalidQueryError
        # by parse_string, so no explicit try/except is needed here.


Example 3 – Handling Mixed Input Types with ``parse``
-----------------------------------------------------

If a plugin accepts a query parameter whose type is only known at runtime
(``str``, ``list[str]``, or a pre-built |Query| instance), dispatching used to
require a three-way ``if`` chain:

.. code-block:: python
    :caption: Before – manual dispatch

    def fetch_songs(query):
        if isinstance(query, Query):
            q, sort = query, NullSort()
        elif isinstance(query, (list, tuple)):
            q, sort = parse_query_parts(list(query), Item)
        elif isinstance(query, str):
            q, sort = parse_query_string(query, Item)
        else:
            raise TypeError(...)

The unified :meth:`~QueryNormalizationContext.parse` entry point collapses
this into a single call:

.. code-block:: python
    :caption: After – unified dispatch

    from beets.library import Item, build_query_context

    def fetch_songs(query):
        ctx = build_query_context()
        q, sort = ctx.parse(query, Item)
        # sort is ``None`` when the caller passed a pre-built Query object.


.. _querynorm-build-context:

Customising the Context with ``build_query_context``
====================================================

For most callers the zero-argument :class:`QueryNormalizationContext`
constructor or ``build_query_context()`` with no arguments is exactly what you
want. The factory accepts three optional keyword arguments for scenarios where
the defaults need to be overridden:

.. rst-class:: plain

``extra_prefixes: dict[str, FieldQueryType] | None``
    Additional prefix entries to merge on top of the built-in + plugin map.

    **Use when:** your plugin introduces a private prefix that is **not**
    registered via :func:`beets.plugins.queries` (for example because it
    should only be valid for one specific plugin).

    .. code-block:: python

        from beets.dbcore.query import SubstringQuery
        from beets.library import build_query_context

        class PrefixPlugin:
            def rule_for(self, pattern: str):
                class FuzzyQuery(SubstringQuery):
                    pass

                ctx = build_query_context(extra_prefixes={"~": FuzzyQuery})
                return ctx.parse_string(pattern, Item)


.. rst-class:: plain

``case_insensitive: bool | None``
    Override the ``sort_case_insensitive`` setting for a particular query.

    **Use when:** a plugin needs deterministic, locale-independent ordering
    regardless of the user's global setting – for instance, when comparing
    query results across machines in a test or when generating a stable key.


.. rst-class:: plain

``apply_normalization: bool``
    Disable :func:`normalize_query_parts` altogether.

    **Use when:** you are re-parsing part strings that have *already* been
    through the normalization step, or when you deliberately want to prevent
    implicit path detection (for example in a query-tutorial plugin that
    exercises raw ``parse_query_part`` semantics).


All three overrides can be combined freely – for example
``build_query_context(extra_prefixes={"#": FuzzyQuery}, case_insensitive=False)``
yields a context that merges the ``#`` prefix while also switching to
case-sensitive sorting.


Error Handling
==============

All builder methods on |QueryNormalizationContext| translate the low-level
:class:`~beets.dbcore.query.InvalidQueryArgumentValueError` exception into
the public :class:`InvalidQueryError` type that the CLI knows how to render.
This is another reason to migrate: when a user types ``bpm:fast`` instead of
a number, plugins using the new pipeline automatically show a nicely
formatted error instead of a traceback.


Deprecation Policy
==================

Starting with beets 2.12 the following call will emit a
:class:`DeprecationWarning`:

.. code-block:: python

    from beets.dbcore import query_from_strings

    query_from_strings(AndQuery, Item, prefixes={}, query_parts=["foo"])
    # → DeprecationWarning: dbcore.query_from_strings() is deprecated;
    #   use beets.library.QueryNormalizationContext.build_collection() …

The low-level helpers in :mod:`beets.dbcore.queryparse` remain functional
for backwards compatibility but will be removed in a future release. New
plugins should always go through the :class:`QueryNormalizationContext` API.
