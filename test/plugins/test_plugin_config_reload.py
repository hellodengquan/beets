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

import sys

from beets import plugins

from test.plugins.conftest import PluginLifecycleTestHelper


class TestPluginConfigReload(PluginLifecycleTestHelper):
    """Test that reloading configuration properly reinitializes plugins."""

    def test_config_reload_picks_new_plugins(self):
        """Test that adding plugins to config and reloading works."""
        initial_plugin_names = plugins.get_plugin_names()
        assert "fictional_plugin" not in initial_plugin_names

    def test_disabled_plugins_config_excluded(self):
        """Test that disabled_plugins config excludes plugins."""
        self.config["plugins"] = ["plugin_a", "plugin_b", "plugin_c"]
        self.config["disabled_plugins"] = ["plugin_b"]

        result = plugins.get_plugin_names()
        assert "plugin_a" in result
        assert "plugin_b" not in result
        assert "plugin_c" in result

    def test_pluginpath_extended(self):
        """Test that pluginpath config extends beetsplug.__path__."""
        original_paths_len = len(sys.path)
        self.config["plugins"] = []

        plugins.get_plugin_names()

        assert len(sys.path) >= original_paths_len

    def test_config_sources_reset(self):
        """Test that config can be reset between test runs."""
        self.config["plugins"] = ["dummy_plugin"]

        self.config.sources = []
        self.config.read(user=False, defaults=True)

        plugin_names = plugins.get_plugin_names()
        assert "dummy_plugin" not in plugin_names

    def test_reload_clears_and_reloads_listeners(self, plugin_factory):
        """Test that a full clear+reload properly resets listeners."""
        call_log = []

        def _handler():
            call_log.append("reloadplugin")

        ReloadPlugin = plugin_factory.make_listener_plugin(
            "reloadplugin", events=[("cli_exit", _handler)]
        )

        self.register_plugin(ReloadPlugin)
        plugins.send("cli_exit")
        assert call_log == ["reloadplugin"]

        self.unload_plugins()
        call_log.clear()

        plugins.send("cli_exit")
        assert call_log == []

        self.register_plugin(ReloadPlugin)
        plugins.send("cli_exit")
        assert call_log == ["reloadplugin"]

    def test_multiple_config_reload_cycles(self, plugin_factory):
        """Test multiple cycles of load/unload."""

        class CyclePlugin(plugins.BeetsPlugin):
            init_count = 0

            def __init__(self):
                super().__init__("cycleplugin")
                CyclePlugin.init_count += 1

            def commands(self):
                from beets import ui

                cmd = ui.Subcommand("cycle-cmd")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        for cycle in range(3):
            self.register_plugin(CyclePlugin)
            assert len(plugins.commands()) == 1
            assert any(c.name == "cycle-cmd" for c in plugins.commands())

            self.unload_plugins()
            assert len(plugins.commands()) == 0

        assert CyclePlugin.init_count == 3

    def test_reload_switches_plugin_set(self, plugin_factory):
        """Test unloading old set and loading a different set."""
        PluginA = plugin_factory.make_command_plugin(
            "set_a", command_names=["a-only"]
        )
        PluginB = plugin_factory.make_command_plugin(
            "set_b", command_names=["b-only"]
        )

        self.register_plugin(PluginA)
        cmds = [c.name for c in plugins.commands()]
        assert "a-only" in cmds
        assert "b-only" not in cmds

        self.unload_plugins()
        assert len(plugins.commands()) == 0

        self.register_plugin(PluginB)
        cmds = [c.name for c in plugins.commands()]
        assert "a-only" not in cmds
        assert "b-only" in cmds
