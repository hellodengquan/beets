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

from typing import ClassVar

import pytest

from beets import plugins, ui
from beets.dbcore import types
from beets.library import Item
from beets.test.helper import IOMixin

from test.plugins.conftest import PluginLifecycleTestHelper


class TestMultiplePluginsEnabled(PluginLifecycleTestHelper):
    """Test that multiple plugins can be enabled simultaneously and
    their commands, listeners, and template functions coexist properly.
    """

    def test_multiple_plugins_commands_registered(self, plugin_factory):
        """Test that commands from multiple plugins are all registered."""
        PluginA = plugin_factory.make_command_plugin(
            "plugina", command_names=["cmd-a"]
        )
        PluginB = plugin_factory.make_command_plugin(
            "pluginb", command_names=["cmd-b"]
        )
        PluginC = plugin_factory.make_command_plugin(
            "pluginc", command_names=["cmd-c1", "cmd-c2"]
        )

        self.register_plugin(PluginA)
        self.register_plugin(PluginB)
        self.register_plugin(PluginC)

        all_commands = plugins.commands()
        command_names = [cmd.name for cmd in all_commands]

        assert "cmd-a" in command_names
        assert "cmd-b" in command_names
        assert "cmd-c1" in command_names
        assert "cmd-c2" in command_names
        assert len(all_commands) == 4

    def test_multiple_plugins_listeners_independent(self, plugin_factory):
        """Test that listeners from different plugins are independent."""
        call_order = []

        PluginAlpha = plugin_factory.make_listener_plugin(
            "pluginalpha", call_log_ref=call_order
        )
        PluginBeta = plugin_factory.make_listener_plugin(
            "pluginbeta", call_log_ref=call_order
        )

        self.register_plugin(PluginAlpha)
        self.register_plugin(PluginBeta)

        plugins.send("cli_exit")

        assert "pluginalpha" in call_order
        assert "pluginbeta" in call_order
        assert len(call_order) == 2

    def test_multiple_plugins_instance_count(self, plugin_factory):
        """Test that each plugin is instantiated exactly once."""

        class PluginOne(plugins.BeetsPlugin):
            instance_count = 0

            def __init__(self):
                super().__init__("pluginone")
                PluginOne.instance_count += 1

        class PluginTwo(plugins.BeetsPlugin):
            instance_count = 0

            def __init__(self):
                super().__init__("plugintwo")
                PluginTwo.instance_count += 1

        self.register_plugin(PluginOne)
        self.register_plugin(PluginTwo)

        assert PluginOne.instance_count == 1
        assert PluginTwo.instance_count == 1

    def test_multiple_plugins_find_plugins_returns_all(self, plugin_factory):
        """Test that find_plugins() returns all registered plugins."""
        PluginX = plugin_factory.make_command_plugin("pluginx")
        PluginY = plugin_factory.make_command_plugin("pluginy")
        PluginZ = plugin_factory.make_command_plugin("pluginz")

        self.register_plugin(PluginX)
        self.register_plugin(PluginY)
        self.register_plugin(PluginZ)

        found_plugins = list(plugins.find_plugins())
        plugin_names = [p.name for p in found_plugins]

        assert len(found_plugins) == 3
        assert "pluginx" in plugin_names
        assert "pluginy" in plugin_names
        assert "pluginz" in plugin_names

    def test_multiple_plugins_template_fields_no_conflict(self, plugin_factory):
        """Test that template fields from different plugins coexist."""
        PluginFieldsA = plugin_factory.make_template_field_plugin(
            "pluginfieldsa", fields={"custom_a": lambda item: "value_a"}
        )
        PluginFieldsB = plugin_factory.make_template_field_plugin(
            "pluginfieldsb", fields={"custom_b": lambda item: "value_b"}
        )

        self.register_plugin(PluginFieldsA)
        self.register_plugin(PluginFieldsB)

        getters = plugins.item_field_getters()
        assert "custom_a" in getters
        assert "custom_b" in getters


class TestDuplicateCommandName(PluginLifecycleTestHelper):
    """Test behavior when multiple plugins register commands with
    the same name.
    """

    def test_duplicate_commands_both_returned(self, plugin_factory):
        """Test that duplicate-named commands are both returned by
        plugins.commands() (first one wins in parser).
        """
        PluginFirst = plugin_factory.make_command_plugin(
            "pluginfirst", command_names=["duplicate-cmd"]
        )
        PluginSecond = plugin_factory.make_command_plugin(
            "pluginsecond", command_names=["duplicate-cmd"]
        )

        self.register_plugin(PluginFirst)
        self.register_plugin(PluginSecond)

        all_commands = plugins.commands()
        dupe_cmds = [c for c in all_commands if c.name == "duplicate-cmd"]

        assert len(dupe_cmds) == 2

    def test_command_aliases_also_conflict(self):
        """Test that command aliases are also tracked."""

        class PluginAlias1(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("pluginalias1")

            def commands(self):
                cmd = ui.Subcommand(
                    "real-name-1", aliases=("alias-conflict",)
                )
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        class PluginAlias2(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("pluginalias2")

            def commands(self):
                cmd = ui.Subcommand(
                    "real-name-2", aliases=("alias-conflict",)
                )
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(PluginAlias1)
        self.register_plugin(PluginAlias2)

        all_commands = plugins.commands()
        aliased_cmds = [
            c for c in all_commands if "alias-conflict" in c.aliases
        ]
        assert len(aliased_cmds) == 2

    def test_same_plugin_multiple_commands_different_names(self):
        """Test that one plugin can register multiple different commands."""

        class MultiCmdPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("multicmdplugin")

            def commands(self):
                results = []
                for i in range(3):
                    cmd = ui.Subcommand(f"multi-cmd-{i}")
                    cmd.func = lambda lib, opts, args, idx=i: idx
                    results.append(cmd)
                return results

        self.register_plugin(MultiCmdPlugin)

        all_commands = plugins.commands()
        for i in range(3):
            assert any(c.name == f"multi-cmd-{i}" for c in all_commands)

    def test_command_conflict_parser_behavior(self):
        """Test the SubcommandsOptionParser picks the first match when
        duplicate names exist.
        """
        parser = ui.SubcommandsOptionParser()

        cmd1 = ui.Subcommand("conflict-cmd")
        cmd1.func = lambda lib, opts, args: "from_cmd1"

        cmd2 = ui.Subcommand("conflict-cmd")
        cmd2.func = lambda lib, opts, args: "from_cmd2"

        parser.add_subcommand(cmd1, cmd2)

        found = parser._subcommand_for_name("conflict-cmd")
        assert found is cmd1

    def test_after_unload_no_conflict_remains(self, plugin_factory):
        """Test that after unloading plugins, conflicting commands
        are removed.
        """
        PluginA = plugin_factory.make_command_plugin(
            "conflicta", command_names=["shared-cmd"]
        )
        PluginB = plugin_factory.make_command_plugin(
            "conflictb", command_names=["shared-cmd"]
        )

        self.register_plugin(PluginA)
        self.register_plugin(PluginB)

        shared_cmds = [
            c for c in plugins.commands() if c.name == "shared-cmd"
        ]
        assert len(shared_cmds) == 2

        self.unload_plugins()
        assert len(plugins.commands()) == 0


class TestPluginDisableCleanup(PluginLifecycleTestHelper):
    """Test that disabling plugins properly cleans up commands,
    listeners, and other plugin-registered resources.
    """

    def test_unload_plugins_clears_instances(self, plugin_factory):
        """Test that unload_plugins clears the plugin instances."""
        CleanupPlugin = plugin_factory.make_command_plugin("cleanupplugin")

        self.register_plugin(CleanupPlugin)
        assert len(list(plugins.find_plugins())) == 1

        self.unload_plugins()
        assert len(list(plugins.find_plugins())) == 0

    def test_unload_plugins_clears_commands(self, plugin_factory):
        """Test that commands are cleared after unloading plugins."""
        CommandPlugin = plugin_factory.make_command_plugin(
            "commandplugin", command_names=["my-temp-cmd"]
        )

        self.register_plugin(CommandPlugin)
        assert len(plugins.commands()) == 1
        assert any(c.name == "my-temp-cmd" for c in plugins.commands())

        self.unload_plugins()
        assert len(plugins.commands()) == 0

    def test_unload_plugins_clears_listeners(self):
        """Test that listeners are cleared after unloading plugins."""

        class ListenerPlugin(plugins.BeetsPlugin):
            called = False

            def __init__(self):
                super().__init__("listenerplugin")
                self.register_listener("cli_exit", self.on_exit)

            def on_exit(self):
                ListenerPlugin.called = True

        self.register_plugin(ListenerPlugin)
        plugins.send("cli_exit")
        assert ListenerPlugin.called is True

        ListenerPlugin.called = False
        self.unload_plugins()

        plugins.send("cli_exit")
        assert ListenerPlugin.called is False

    def test_unload_plugins_clears_raw_listeners(self):
        """Test that _raw_listeners is cleared on unload."""

        class RawListenerPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("rawlistenerplugin")
                self.register_listener("write", lambda: None)

        self.register_plugin(RawListenerPlugin)
        assert len(plugins.BeetsPlugin._raw_listeners["write"]) > 0

        self.unload_plugins()
        assert len(plugins.BeetsPlugin._raw_listeners["write"]) == 0

    def test_unload_plugins_isolates_between_tests(self, plugin_factory):
        """Test that state from one test doesn't leak to next."""
        LeakTestPlugin = plugin_factory.make_command_plugin(
            "leaktestplugin", command_names=["leak-cmd"]
        )

        self.register_plugin(LeakTestPlugin)
        assert len(plugins.commands()) == 1

        self.unload_plugins()

        assert len(plugins.commands()) == 0
        assert len(list(plugins.find_plugins())) == 0

    def test_partial_plugin_disable(self, plugin_factory):
        """Test behavior when some plugins are disabled via config."""
        PluginKeep = plugin_factory.make_command_plugin(
            "pluginkeep", command_names=["keep-cmd"]
        )
        PluginDisable = plugin_factory.make_command_plugin(
            "plugindisable", command_names=["disable-cmd"]
        )

        self.register_plugin(PluginKeep)
        self.register_plugin(PluginDisable)
        assert len(plugins.commands()) == 2

        self.unload_plugins()
        self.register_plugin(PluginKeep)

        all_cmds = plugins.commands()
        cmd_names = [c.name for c in all_cmds]
        assert "keep-cmd" in cmd_names
        assert "disable-cmd" not in cmd_names
        assert len(all_cmds) == 1
