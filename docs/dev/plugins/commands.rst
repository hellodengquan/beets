.. _add_subcommands:

Add Commands to the CLI
=======================

Plugins can add new subcommands to the ``beet`` command-line interface. Define
the plugin class' ``commands()`` method to return a list of ``Subcommand``
objects. (The ``Subcommand`` class is defined in the ``beets.ui`` module.)
Here's an example plugin that adds a simple command:

.. code-block:: python

    from beets.plugins import BeetsPlugin
    from beets.ui import Subcommand

    my_super_command = Subcommand("super", help="do something super")


    def say_hi(lib, opts, args):
        print("Hello everybody! I'm a plugin!")


    my_super_command.func = say_hi


    class SuperPlug(BeetsPlugin):
        def commands(self):
            return [my_super_command]

To make a subcommand, invoke the constructor like so: ``Subcommand(name, parser,
help, aliases)``. The ``name`` parameter is the only required one and should
just be the name of your command. ``parser`` can be an `OptionParser instance`_,
but it defaults to an empty parser (you can extend it later). ``help`` is a
description of your command, and ``aliases`` is a list of shorthand versions of
your command name.

.. _optionparser instance: https://docs.python.org/3/library/optparse.html

You'll need to add a function to your command by saying ``mycommand.func =
myfunction``. This function should take the following parameters: ``lib`` (a
beets ``Library`` object) and ``opts`` and ``args`` (command-line options and
arguments as returned by OptionParser.parse_args_).

.. _optionparser.parse_args: https://docs.python.org/3/library/optparse.html#parsing-arguments

The function should use any of the utility functions defined in ``beets.ui``.
Try running ``pydoc beets.ui`` to see what's available.

You can add command-line options to your new command using the ``parser`` member
of the ``Subcommand`` class, which is a ``CommonOptionsParser`` instance. Just
use it like you would a normal ``OptionParser`` in an independent script. Note
that it offers several methods to add common options: ``--album``, ``--path``
and ``--format``. This feature is versatile and extensively documented, try
``pydoc beets.ui.CommonOptionsParser`` for more information.


.. _command-name-conflicts:

Command Name Conflicts
======================

When a plugin registers a command whose name or any of its aliases collides with
an existing command (either built-in or provided by another plugin), the
following rules apply:

Which Command Takes Effect
~~~~~~~~~~~~~~~~~~~~~~~

- **Registration order determines precedence**: the first command to be registered
  remains effective; any later command with the same name or alias is ignored.
- **Built-in commands are always registered before any plugin commands, so a
  plugin can never override a built-in command.
- **When two plugins register the same command name, whichever plugin is
  loaded first wins. Plugin load order depends on the order in which
  plugins are listed in the configuration file.

Conflict Warning Log
~~~~~~~~~~~~~~~~~~~~

When a command is ignored due to a name or alias conflict, a warning
is logged at the WARNING level with the following format::

    command 'list' from plugin 'myplugin' conflicts with built-in and has been ignored
    command 'sharedcmd' from plugin 'plugbeta' conflicts with plugin 'plugalpha' and has been ignored

The log message includes:

- The colliding name (the command name or alias that caused the conflict)
- The source of the new command being ignored (``plugin '<plugin_name>'`` or ``built-in``)
- The source of the existing command that remains effective

Help Output
~~~~~~~~~~~

The ``beet`` help output labels each command with its source:

- Plugin-provided commands are annotated with ``[plugin: <plugin_name>]`` after
  their help text.
- Built-in commands have no annotation.

Because conflicting commands are not added to the command list, the help
output never shows duplicate entries for the same command name.

Example
~~~~~

If two plugins both register a command named ``dupcmd``:

- The first plugin's command appears in ``beet`` help as::

    dupcmd  does something [plugin: plugalpha]

- The second plugin's ``dupcmd`` is ignored, and a warning is logged::

    WARNING: command 'dupcmd' from plugin 'plugbeta' conflicts with plugin 'plugalpha' and has been ignored
