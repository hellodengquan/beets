# This file is part of beets.
# Copyright 2016, Thomas Scholtes.
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

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from beets import plugins, ui

from test.plugins.conftest import PluginLifecycleTestHelper


class TestPluginCommandDispatch(PluginLifecycleTestHelper):
    """Test full CLI dispatch of plugin commands: from command-line args,
    through parsing, to execution.

    These tests go through ``beets.ui.SubcommandsOptionParser`` and
    ``beets.ui._raw_main`` just like the real beet command does,
    verifying that plugin-registered commands are discovered, parsed
    correctly, and their handlers invoked with the expected arguments.
    """

    @pytest.fixture(autouse=True)
    def _setup_env_and_patch(self, setup):
        """Ensure the base setup fixture runs and patch _open_library."""
        with patch("beets.ui._open_library", return_value=self.lib):
            yield

    def test_plugin_command_invoked_via_raw_main(self, plugin_factory):
        """A plugin's command is discoverable and callable through the
        full CLI path (``_raw_main``).
        """
        handler = Mock()

        def _func(lib, opts, args):
            handler(lib, opts, args)

        Plugin = plugin_factory.make_command_plugin(
            "greeter",
            command_names=["greet"],
            command_funcs={"greet": _func},
        )
        self.register_plugin(Plugin)

        with patch.object(self.lib, "_close", Mock()):
            ui._raw_main(["greet", "arg1", "arg2"])

        assert handler.called
        lib_arg, opts_arg, args_arg = handler.call_args[0]
        assert lib_arg is self.lib
        assert args_arg == ["arg1", "arg2"]

    def test_plugin_command_with_custom_options_parsed(self):
        """Plugin command options are parsed from command-line args."""
        captured_opts = None
        captured_args = None

        class OptPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("optplugin")

            def commands(self):
                cmd = ui.Subcommand("optcmd", help="command with options")
                cmd.parser.add_option(
                    "-f", "--flag", dest="flag", action="store_true"
                )
                cmd.parser.add_option(
                    "-n", "--name", dest="name", type="string"
                )

                def func(lib, opts, args):
                    nonlocal captured_opts, captured_args
                    captured_opts = opts
                    captured_args = args

                cmd.func = func
                return [cmd]

        self.register_plugin(OptPlugin)

        with patch.object(self.lib, "_close", Mock()):
            ui._raw_main(["optcmd", "--flag", "--name", "hello", "rest"])

        assert captured_opts is not None
        assert captured_opts.flag is True
        assert captured_opts.name == "hello"
        assert captured_args == ["rest"]

    def test_multiple_plugin_commands_dispatched_independently(self, plugin_factory):
        """Two different plugin commands both route to their handlers."""
        results = {}

        def make_handler(name):
            def _h(lib, opts, args):
                results[name] = list(args)

            return _h

        PluginA = plugin_factory.make_command_plugin(
            "plugin_a",
            command_names=["cmd-a"],
            command_funcs={"cmd-a": make_handler("a")},
        )
        PluginB = plugin_factory.make_command_plugin(
            "plugin_b",
            command_names=["cmd-b"],
            command_funcs={"cmd-b": make_handler("b")},
        )

        self.register_plugin(PluginA)
        self.register_plugin(PluginB)

        with patch.object(self.lib, "_close", Mock()):
            ui._raw_main(["cmd-a", "foo"])
            ui._raw_main(["cmd-b", "bar", "baz"])

        assert results == {"a": ["foo"], "b": ["bar", "baz"]}

    def test_plugin_command_alias_dispatched(self):
        """A plugin command can also be invoked by its alias."""
        captured = []

        class AliasPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("aliasplugin")

            def commands(self):
                cmd = ui.Subcommand(
                    "realname", aliases=("rn", "alias2"), help="has aliases"
                )

                def func(lib, opts, args):
                    captured.append(("realname", list(args)))

                cmd.func = func
                return [cmd]

        self.register_plugin(AliasPlugin)

        with patch.object(self.lib, "_close", Mock()):
            ui._raw_main(["realname", "via-name"])
            ui._raw_main(["rn", "via-alias"])
            ui._raw_main(["alias2", "via-alias2"])

        assert captured == [
            ("realname", ["via-name"]),
            ("realname", ["via-alias"]),
            ("realname", ["via-alias2"]),
        ]

    def test_plugin_command_appears_in_help_listing(self, plugin_factory):
        """Plugin commands show up in the parser's help output."""
        Plugin = plugin_factory.make_command_plugin(
            "helpmepls", command_names=["mytestcmd"]
        )
        self.register_plugin(Plugin)

        parser = ui.SubcommandsOptionParser()
        from beets.ui.commands import default_commands

        parser.add_subcommand(*default_commands)
        parser.add_subcommand(*plugins.commands())

        help_text = parser.format_help()
        assert "mytestcmd" in help_text
        assert "Command from helpmepls" in help_text

    def test_unknown_plugin_command_raises_user_error(self, plugin_factory):
        """A command that doesn't exist raises UserError."""
        Plugin = plugin_factory.make_command_plugin(
            "noexist", command_names=["existing"]
        )
        self.register_plugin(Plugin)

        with (
            patch.object(self.lib, "_close", Mock()),
            pytest.raises(ui.UserError, match="unknown command"),
        ):
            ui._raw_main(["definitely-not-command"])

    def test_plugin_command_receives_library_object(self, plugin_factory):
        """The dispatched command receives the library instance."""
        received_lib = [None]

        def _handler(lib, opts, args):
            received_lib[0] = lib

        Plugin = plugin_factory.make_command_plugin(
            "libcheck",
            command_names=["libcheck"],
            command_funcs={"libcheck": _handler},
        )
        self.register_plugin(Plugin)

        with patch.object(self.lib, "_close", Mock()):
            ui._raw_main(["libcheck"])

        assert received_lib[0] is self.lib

    def test_cli_exit_event_sent_after_plugin_command(self, plugin_factory):
        """The cli_exit event fires after a plugin command runs."""
        event_log = []

        class EventPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("eventplugin")
                self.register_listener("cli_exit", self._on_exit)

            def _on_exit(self, **kwargs):
                event_log.append("cli_exit fired")

            def commands(self):
                cmd = ui.Subcommand("eventcmd")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(EventPlugin)

        with patch.object(self.lib, "_close", Mock()):
            ui._raw_main(["eventcmd"])

        assert "cli_exit fired" in event_log

    def test_parse_subcommand_returns_subcommand_object(self, plugin_factory):
        """SubcommandsOptionParser.parse_subcommand returns the correct
        subcommand, options, and remaining args.
        """
        Plugin = plugin_factory.make_command_plugin(
            "parserplugin", command_names=["parsetest"]
        )
        self.register_plugin(Plugin)

        parser = ui.SubcommandsOptionParser()
        parser.add_subcommand(*plugins.commands())

        subcmd, subopts, subargs = parser.parse_subcommand(
            ["parsetest", "extra1", "extra2"]
        )

        assert subcmd.name == "parsetest"
        assert subargs == ["extra1", "extra2"]


class TestPluginCommandNameConflict(PluginLifecycleTestHelper):
    """Test the behavior when a plugin registers a command whose name
    collides with a built-in command or another plugin's command.

    Conflict resolution:
        - built-in commands are registered first, so they shadow plugin commands
        - first-registered plugin command shadows later ones with the same name
        - a warning is logged explaining which command was ignored and why
        - help output only shows the effective command, annotated with source
    """

    @pytest.fixture(autouse=True)
    def _patch_lib(self, setup):
        with patch("beets.ui._open_library", return_value=self.lib):
            yield

    def test_builtin_shadows_plugin_command_same_name(self):
        """When a plugin command shares a name with a built-in,
        the built-in one wins (is dispatched first).

        This matches the current ordering in ``_setup``::

            subcommands = list(default_commands)
            subcommands.extend(plugins.commands())
        """
        builtin_called = [False]
        plugin_called = [False]

        builtin_cmd = ui.Subcommand("list", help="built-in list")

        def builtin_func(lib, opts, args):
            builtin_called[0] = True

        builtin_cmd.func = builtin_func

        class PluginDupe(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("dupeplugin")

            def commands(self):
                cmd = ui.Subcommand("list", help="plugin list")

                def plugin_func(lib, opts, args):
                    plugin_called[0] = True

                cmd.func = plugin_func
                return [cmd]

        self.register_plugin(PluginDupe)

        parser = ui.SubcommandsOptionParser()
        parser.add_subcommand(builtin_cmd)
        parser.add_subcommand(*plugins.commands())

        subcmd, subopts, subargs = parser.parse_subcommand(["list"])
        assert subcmd is builtin_cmd

        subcmd.func(self.lib, subopts, subargs)
        assert builtin_called[0] is True
        assert plugin_called[0] is False

    def test_plugin_conflict_with_builtin_logs_warning(self, caplog):
        """When a plugin command collides with a built-in, a warning
        is logged identifying the ignored command and its plugin.
        """
        import logging

        builtin_cmd = ui.Subcommand("list", help="built-in")
        builtin_cmd.func = lambda lib, opts, args: None

        class PluginDupe(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("myplugin")

            def commands(self):
                cmd = ui.Subcommand("list", help="plugin")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(PluginDupe)

        caplog.set_level(logging.WARNING)

        parser = ui.SubcommandsOptionParser()
        parser.add_subcommand(builtin_cmd)
        parser.add_subcommand(*plugins.commands())

        warning_msgs = [
            rec.message
            for rec in caplog.records
            if rec.levelno >= logging.WARNING
        ]
        conflict_warnings = [
            msg for msg in warning_msgs
            if "conflicts" in msg and "ignored" in msg
        ]
        assert len(conflict_warnings) == 1
        assert "list" in conflict_warnings[0]
        assert "myplugin" in conflict_warnings[0]
        assert "built-in" in conflict_warnings[0]

    def test_plugin_conflict_with_another_plugin_logs_warning(self, caplog):
        """When two plugins register the same command name, a warning
        is logged with both plugin names.
        """
        import logging

        class PluginA(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("plugalpha")

            def commands(self):
                cmd = ui.Subcommand("sharedcmd", help="from alpha")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        class PluginB(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("plugbeta")

            def commands(self):
                cmd = ui.Subcommand("sharedcmd", help="from beta")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(PluginA)
        self.register_plugin(PluginB)

        caplog.set_level(logging.WARNING)

        parser = ui.SubcommandsOptionParser()
        parser.add_subcommand(*plugins.commands())

        warning_msgs = [
            rec.message
            for rec in caplog.records
            if rec.levelno >= logging.WARNING
        ]
        conflict_warnings = [
            msg for msg in warning_msgs
            if "conflicts" in msg and "ignored" in msg
        ]
        assert len(conflict_warnings) == 1
        assert "sharedcmd" in conflict_warnings[0]
        assert "plugbeta" in conflict_warnings[0]
        assert "plugalpha" in conflict_warnings[0]

    def test_alias_conflict_logs_warning(self, caplog):
        """When a plugin's command alias collides, a warning is also logged."""
        import logging

        class PluginPrimary(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("plug_primary")

            def commands(self):
                cmd = ui.Subcommand("real", aliases=("r",))
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        class PluginAlias(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("plug_alias")

            def commands(self):
                cmd = ui.Subcommand("other", aliases=("r",))
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(PluginPrimary)
        self.register_plugin(PluginAlias)

        caplog.set_level(logging.WARNING)

        parser = ui.SubcommandsOptionParser()
        parser.add_subcommand(*plugins.commands())

        conflict_msgs = [
            rec.message for rec in caplog.records
            if rec.levelno >= logging.WARNING and "conflicts" in rec.message
        ]
        assert len(conflict_msgs) == 1
        assert "'r'" in conflict_msgs[0]

    def test_first_plugin_command_shadows_later_plugin_commands(self):
        """When two plugins register commands with the same name,
        the one registered first wins and the second is not added.
        """
        results = {"first": False, "second": False}

        class FirstPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("firstplugin")

            def commands(self):
                cmd = ui.Subcommand("shared")

                def func(lib, opts, args):
                    results["first"] = True

                cmd.func = func
                return [cmd]

        class SecondPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("secondplugin")

            def commands(self):
                cmd = ui.Subcommand("shared")

                def func(lib, opts, args):
                    results["second"] = True

                cmd.func = func
                return [cmd]

        self.register_plugin(FirstPlugin)
        self.register_plugin(SecondPlugin)

        parser = ui.SubcommandsOptionParser()
        parser.add_subcommand(*plugins.commands())

        subcmd, subopts, subargs = parser.parse_subcommand(["shared"])
        subcmd.func(self.lib, subopts, subargs)

        assert results["first"] is True
        assert results["second"] is False

    def test_conflicting_command_not_added_to_subcommands(self):
        """The conflicting (second) command is *not* added to the parser's
        subcommands list -- only the first one remains.
        """
        class PluginA(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("plug_a")

            def commands(self):
                cmd = ui.Subcommand("doublename")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        class PluginB(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("plug_b")

            def commands(self):
                cmd = ui.Subcommand("doublename")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(PluginA)
        self.register_plugin(PluginB)

        parser = ui.SubcommandsOptionParser()
        parser.add_subcommand(*plugins.commands())

        same_name_cmds = [
            c for c in parser.subcommands if c.name == "doublename"
        ]
        assert len(same_name_cmds) == 1
        assert same_name_cmds[0].plugin == "plug_a"

    def test_alias_conflict_primary_name_wins(self):
        """If a plugin command's primary name collides with another
        plugin command's alias, whichever was added earlier wins
        (because ``_subcommand_for_name`` returns the first match).
        """
        called = {"primary": False, "other": False}

        class PluginOne(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("one")

            def commands(self):
                cmd = ui.Subcommand("primary", aliases=("p1",))
                cmd.func = lambda lib, opts, args: called.__setitem__(
                    "primary", True
                )
                return [cmd]

        class PluginTwo(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("two")

            def commands(self):
                cmd = ui.Subcommand("other", aliases=("primary",))
                cmd.func = lambda lib, opts, args: called.__setitem__(
                    "other", True
                )
                return [cmd]

        self.register_plugin(PluginOne)
        self.register_plugin(PluginTwo)

        parser = ui.SubcommandsOptionParser()
        parser.add_subcommand(*plugins.commands())

        subcmd, subopts, subargs = parser.parse_subcommand(["primary"])
        assert subcmd.name == "primary"
        assert subcmd.aliases == ("p1",)

        subcmd.func(self.lib, subopts, subargs)
        assert called["primary"] is True
        assert called["other"] is False

    def test_raw_main_dispatches_builtin_over_plugin(self):
        """End-to-end: ``_raw_main`` dispatches to built-in command when
        plugin command has same name (because built-ins come first).
        """
        import beets.ui.commands as cmds

        builtin_list_handler = None
        for cmd in cmds.default_commands:
            if cmd.name == "list":
                builtin_list_handler = cmd.func
                break

        assert builtin_list_handler is not None

        plugin_list_called = [False]

        class ShadowPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("shadowplugin")

            def commands(self):
                cmd = ui.Subcommand("list")

                def func(lib, opts, args):
                    plugin_list_called[0] = True

                cmd.func = func
                return [cmd]

        self.register_plugin(ShadowPlugin)

        with patch.object(self.lib, "_close", Mock()):
            ui._raw_main(["list"])

        assert plugin_list_called[0] is False

    def test_help_shows_only_effective_command(self):
        """Help output only shows one entry per command name (the one
        that is actually effective) because duplicates are rejected.
        """
        class PluginOne(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("one")

            def commands(self):
                cmd = ui.Subcommand("duphelp", help="from plugin one")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        class PluginTwo(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("two")

            def commands(self):
                cmd = ui.Subcommand("duphelp", help="from plugin two")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(PluginOne)
        self.register_plugin(PluginTwo)

        all_cmds = plugins.commands()
        parser = ui.SubcommandsOptionParser()
        parser.add_subcommand(*all_cmds)

        help_text = parser.format_help()
        assert help_text.count("duphelp") == 1
        assert "from plugin one" in help_text
        assert "from plugin two" not in help_text

    def test_help_annotates_plugin_command_with_source(self):
        """Plugin commands in help output are tagged with the plugin name
        so the user can tell which plugin provides each command.
        """

        class HelpPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("mygreatplugin")

            def commands(self):
                cmd = ui.Subcommand("greatcmd", help="does great things")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(HelpPlugin)

        parser = ui.SubcommandsOptionParser()
        parser.add_subcommand(*plugins.commands())

        help_text = parser.format_help()
        assert "greatcmd" in help_text
        assert "[plugin: mygreatplugin]" in help_text
        assert "does great things" in help_text

    def test_help_builtin_commands_have_no_plugin_tag(self):
        """Built-in commands should not show a [plugin: ...] tag."""
        parser = ui.SubcommandsOptionParser()
        from beets.ui.commands import default_commands

        parser.add_subcommand(*default_commands)

        help_text = parser.format_help()
        assert "[plugin:" not in help_text
        assert "list" in help_text
        assert "import" in help_text
