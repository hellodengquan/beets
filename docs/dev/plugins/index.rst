Plugin Development
==================

Beets plugins are Python modules or packages that extend the core functionality
of beets. The plugin system is designed to be flexible, allowing developers to
add virtually any type of features to beets.

For instance you can create plugins that add new commands to the command-line
interface, listen for events in the beets lifecycle or extend the autotagger
with new metadata sources.

.. _basic-plugin-setup:

Basic Plugin Setup
------------------

A beets plugin is just a Python module or package inside the ``beetsplug``
namespace [1]_ package. To create the basic plugin layout, create a directory
called ``beetsplug`` and add either your plugin module:

.. code-block:: shell

    beetsplug/
    └── myawesomeplugin.py

or your plugin subpackage

.. code-block:: shell

    beetsplug/
    └── myawesomeplugin/
        ├── __init__.py
        └── myawesomeplugin.py

.. attention::

    You do not need to add an ``__init__.py`` file to the ``beetsplug``
    directory. Python treats your plugin as a namespace package automatically,
    thus we do not depend on ``pkgutil``-based setup in the ``__init__.py`` file
    anymore.

The meat of your plugin goes in ``myawesomeplugin.py``. Every plugin has to
extend the |BeetsPlugin| abstract base class [2]_ . For instance, a minimal
plugin without any functionality would look like this:

.. code-block:: python

    # beetsplug/myawesomeplugin.py
    from beets.plugins import BeetsPlugin


    class MyAwesomePlugin(BeetsPlugin):
        pass

.. attention::

    If your plugin is composed of intermediate |BeetsPlugin| subclasses, make
    sure that your plugin is defined *last* in the namespace. We only load the
    last subclass of |BeetsPlugin| we find in your plugin namespace.

To use your new plugin, you need to package [3]_ your plugin and install it into
your ``beets`` (virtual) environment. To enable your plugin, add it it to the
beets configuration

.. code-block:: yaml

    # config.yaml
    plugins:
      - myawesomeplugin

and you're good to go!

.. [1] Check out `this article`_ and `this Stack Overflow question`_ if you
    haven't heard about namespace packages.

.. [2] Abstract base classes allow us to define a contract which any plugin must
    follow. This is a common paradigm in object-oriented programming, and it
    helps to ensure that plugins are implemented in a consistent way. For more
    information, see for example pep-3119_.

.. [3] There are a variety of packaging tools available for python, for example
    you can use poetry_, setuptools_ or hatchling_.

.. _hatchling: https://hatch.pypa.io/latest/config/build/#build-system

.. _pep-3119: https://peps.python.org/pep-3119/#rationale

.. _poetry: https://python-poetry.org/docs/pyproject/#packages

.. _setuptools: https://setuptools.pypa.io/en/latest/userguide/package_discovery.html#finding-simple-packages

.. _this article: https://realpython.com/python-namespace-package/#setting-up-some-namespace-packages

.. _this stack overflow question: https://stackoverflow.com/questions/1675734/how-do-i-create-a-namespace-package-in-python/27586272#27586272

.. _plugin-config-schema:

Plugin Config Schema
--------------------

Starting with beets 2.x, plugins should declare their configuration options
using the ``register_config()`` and ``register_config_batch()`` APIs on
``BeetsPlugin``. These methods replace the legacy ``self.config.add(...)``
pattern and attach metadata to each configuration option.  Metadata enables
automatic type coercion, input validation, sensible error messages, sensitive
value redactions, deprecation warnings and, eventually, auto-generated
documentation.

Quick Start
^^^^^^^^^^^

Every configuration key is declared with a dictionary of metadata.  For
options that share a common structure you can batch them:

.. code-block:: python

    from beets.plugins import BeetsPlugin


    class MyAwesomePlugin(BeetsPlugin):
        def __init__(self):
            super().__init__()
            self.register_config_batch(
                {
                    "host": {
                        "default": "localhost",
                        "type": str,
                        "help": "Hostname of the remote service.",
                    },
                    "port": {
                        "default": 8080,
                        "type": int,
                        "help": "TCP port of the remote service.",
                    },
                    "api_key": {
                        "default": "",
                        "type": str,
                        "help": "Authentication token.",
                        "redact": True,
                    },
                    "mode": {
                        "default": "safe",
                        "type": str,
                        "choices": ["safe", "aggressive", "dry-run"],
                        "help": "Operation mode.",
                    },
                }
            )

Use the type-safe getters instead of the raw ``self.config[key].get(...)``
calls:

* ``self.config_int(key)`` -> ``int``
* ``self.config_bool(key)`` -> ``bool``
* ``self.config_str(key)`` -> ``str``
* ``self.config_float(key)`` -> ``float``
* ``self.get_config(key, type=...)`` -> generic getter with optional
  override of the registered type.

The getters raise a consistent ``PluginConfigError`` if the value cannot be
coerced, and the ``validate()`` hook runs automatically once all plugins
have been loaded, reporting all misconfigured keys together.

Metadata Keys
^^^^^^^^^^^^^

Each configuration option accepts the following eight metadata keys.  Only
``default`` is required – everything else is optional but strongly
encouraged.

.. list-table::
   :header-rows: 1
   :widths: 20 15 65

   * - Key
     - Type
     - Description

   * - **default** *(required)*
     - ``Any``
     - The default value used when the user does not provide one.  Use
       ``None`` for optional options that have no sensible fallback.

   * - **type**
     - ``type`` | ``confuse.Template``
     - Expected Python type (``int``, ``str``, ``bool``, ``float``,
       ``list``, ...) or a ``confuse`` template such as
       ``confuse.OneOf([...])``.  The type-safe getters automatically pass
       this value to confuse so the user is presented with a clear error
       on mismatch.

   * - **choices**
     - ``list[Any]``
     - A list of permitted values.  Validation fails with a clear error if
       the user-provided value is not in the list.  This is typically used
       together with ``type=str`` for enumeration-style options.

   * - **help**
     - ``str``
     - Human-readable description of the option.  Descriptions are used for
       auto-generated documentation and error messages, so keep them short
       and precise.

   * - **required**
     - ``bool``
     - If ``True``, ``validate()`` raises a ``PluginConfigError`` unless
       the user has explicitly set a value.  Defaults to ``False``.

   * - **validator**
     - ``Callable[[Any], bool]``
     - Arbitrary predicate that is called with the resolved value *after*
       the type check.  Return ``True`` to accept the value, ``False`` (or
       raise an exception) to reject it.

   * - **deprecated**
     - ``bool``
     - If ``True`` and the user explicitly configured the key, a
       ``DeprecationWarning`` is emitted on plugin load.  Pair with
       ``deprecation_message`` to explain what users should do instead.

   * - **redact**
     - ``bool``
     - Marks the value as sensitive (passwords, tokens, API keys).  beets
       sets ``confuse``’s ``redact`` flag so the value is masked in
       configuration dumps and logs.  Use this together with
       ``required=True`` for credentials.

Migration Reference
^^^^^^^^^^^^^^^^^^^

The following table summarises the mapping from the legacy
``self.config.add(...)`` pattern to the new ``register_config_batch()``
style together with the recommended type-safe getter:

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Old style
     - New style

   * - ``self.config.add({"auto": True})``

       ``... if self.config["auto"]:``
     - ``self.register_config_batch({"auto": {"default": True,``
       ``"type": bool, "help": "..."}})``

       ``... if self.config_bool("auto"):``

   * - ``self.config.add({"threads": 4})``

       ``self.config["threads"].get(int)``
     - ``self.register_config_batch({"threads": {"default": 4,``
       ``"type": int, "help": "..."}})``

       ``self.config_int("threads")``

   * - ``self.config.add({"backend": "cmd"})``

       ``self.config["backend"].as_choice(BACKENDS)``
     - ``self.register_config_batch({"backend": {"default": "cmd",``
       ``"type": str, "choices": list(BACKENDS), "help": "..."}})``

       ``self.config_str("backend")`` (choices validated on load)

   * - ``self.config.add({"password": ""})``

       ``self.config["password"].redact = True``
     - ``self.register_config_batch({"password": {"default": "",``
       ``"type": str, "help": "...", "redact": True}})``

       ``self.config_str("password")``

   * - ``self.config.add({"threshold": 0.0})``

       ``self.config["threshold"].get(float)``
     - ``self.register_config_batch({"threshold": {"default": 0.0,``
       ``"type": float, "help": "..."}})``

       ``self.config_float("threshold")``

   * - ``self.config.add({"source_weight": 0.5})``
     - (marked deprecated)

       ``self.register_config_batch({"source_weight":``
       ``{"default": 0.5, "type": float, "deprecated": True,``
       ``"deprecation_message": "Use data_source_mismatch_penalty"}})``

Migrated Core Plugins
^^^^^^^^^^^^^^^^^^^^^

The following high-usage plugins in ``beetsplug/`` already declare their
configuration via ``register_config_batch()`` and can serve as real-world
references when porting the remaining plugins:

* ``embedart`` – auto-embedding workflow.
* ``lastgenre`` – genre lookups with weighted tags.
* **lastfm** (via ``fetchart.LastFM``) – MusicBrainz / Last.fm cover art
  integration; demonstrates ``choices`` and external credential
  ``redact``.
* ``mpdupdate`` – MPD notification; demonstrates environment-variable
  overrides in defaults and the ``redact`` flag for passwords.
* ``replaygain`` – ReplayGain analysis with ten options, including
  ``choices`` for the backend and peak detection method.
* ``scrub`` – minimal single-boolean example.
* ``convert`` – ~20 options ranging from simple booleans and integers to
  nested format dictionaries.

Porting Checklist
^^^^^^^^^^^^^^^^^

When migrating a plugin, perform the following steps in order:

1. Replace ``self.config.add({...})`` with ``self.register_config_batch({...})``
   and wrap every default value in a metadata dictionary, adding at least
   ``type`` and ``help``.
2. For enumerations, set ``choices=list(ALLOWED_VALUES)``.
3. For secrets/tokens, add ``redact=True``.
4. For options the user *must* provide, add ``required=True``.
5. Replace raw ``self.config[key].get(SomeType)`` calls with
   ``self.config_<type>(key)``.
6. Leave options that rely on advanced confuse templates
   (``confuse.OneOf([bool, String(...)])``, ``confuse.Optional(Filename())``,
   ...) on the legacy getter – the registration still captures the default,
   help text and redactions for documentation purposes.
7. Run the plugin’s existing tests plus the generic
   ``test/test_plugin_config_manager.py`` suite.

More information
----------------

For more information on writing plugins, feel free to check out the following
resources:

.. toctree::
    :maxdepth: 3
    :includehidden:

    commands
    events
    autotagger
    other/index
