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
from pathlib import Path
from unittest.mock import patch

import pytest

import beets.plugins
from beets.plugins import (
    BeetsPlugin,
    PluginImportError,
    find_plugins,
    load_plugins,
)

from test.plugins.conftest import (
    PluginLifecycleTestHelper,
    write_real_plugin,
)


class TestRealPluginLoadFromDirectory(PluginLifecycleTestHelper):
    """Test loading real plugin packages from a plugin directory
    and verify command registration and cleanup on disable.

    These tests exercise the full plugin discovery path:
    beets.plugins.get_plugin_names() -> _get_plugin() -> find the
    BeetsPlugin subclass in the module -> instantiate.
    """

    @pytest.fixture(autouse=True)
    def _cleanup_imported_modules(self):
        """Ensure dynamically imported beetsplug.* modules are cleaned up
        between tests to avoid stale module cache.
        """
        test_plugins = {
            "alphareal", "firstreal", "secondreal", "keepreal", "skipreal",
            "gammareal", "deltareal", "epsilonreal", "from_a", "from_b",
            "zetareal",
        }
        pre_modules = set(sys.modules.keys())
        yield
        new_modules = set(sys.modules.keys()) - pre_modules
        for mod in list(new_modules):
            if mod.startswith("beetsplug.") and any(
                p in mod for p in test_plugins
            ):
                del sys.modules[mod]

    def test_load_single_real_plugin_commands_registered(self, real_plugin_factory):
        """Test loading a single real plugin from disk exposes its commands."""
        beetsplug_dir, create_plugin = real_plugin_factory

        create_plugin("alphareal", command_names=["alpha-real-cmd"])

        self.config["pluginpath"] = [str(beetsplug_dir)]
        self.config["plugins"] = ["alphareal"]

        load_plugins()

        loaded_names = [p.name for p in find_plugins()]
        assert "alphareal" in loaded_names

        cmd_names = [c.name for c in beets.plugins.commands()]
        assert "alpha-real-cmd" in cmd_names

    def test_load_multiple_real_plugins(self, real_plugin_factory):
        """Test loading multiple real plugins from the same directory."""
        beetsplug_dir, create_plugin = real_plugin_factory

        create_plugin("firstreal", command_names=["first-cmd"])
        create_plugin("secondreal", command_names=["second-cmd", "second-extra"])

        self.config["pluginpath"] = [str(beetsplug_dir)]
        self.config["plugins"] = ["firstreal", "secondreal"]

        load_plugins()

        loaded_names = sorted(p.name for p in find_plugins())
        assert loaded_names == ["firstreal", "secondreal"]

        cmd_names = [c.name for c in beets.plugins.commands()]
        assert "first-cmd" in cmd_names
        assert "second-cmd" in cmd_names
        assert "second-extra" in cmd_names

    def test_disabled_real_plugin_not_loaded(self, real_plugin_factory):
        """Test that disabled_plugins config excludes real plugins."""
        beetsplug_dir, create_plugin = real_plugin_factory

        create_plugin("keepreal", command_names=["keep-cmd"])
        create_plugin("skipreal", command_names=["skip-cmd"])

        self.config["pluginpath"] = [str(beetsplug_dir)]
        self.config["plugins"] = ["keepreal", "skipreal"]
        self.config["disabled_plugins"] = ["skipreal"]

        load_plugins()

        loaded_names = [p.name for p in find_plugins()]
        assert "keepreal" in loaded_names
        assert "skipreal" not in loaded_names

        cmd_names = [c.name for c in beets.plugins.commands()]
        assert "keep-cmd" in cmd_names
        assert "skip-cmd" not in cmd_names

    def test_unload_real_plugins_clears_commands(self, real_plugin_factory):
        """Test that unloading removes commands from real plugins."""
        beetsplug_dir, create_plugin = real_plugin_factory

        create_plugin("gammareal", command_names=["gamma-cmd"])

        self.config["pluginpath"] = [str(beetsplug_dir)]
        self.config["plugins"] = ["gammareal"]

        load_plugins()
        assert any(c.name == "gamma-cmd" for c in beets.plugins.commands())

        self.unload_plugins()

        assert len(list(find_plugins())) == 0
        assert len(beets.plugins.commands()) == 0
        assert len(BeetsPlugin.listeners["pluginload"]) == 0

    def test_reload_real_plugin_after_unload(self, real_plugin_factory):
        """Test full cycle: load -> unload -> load again works."""
        beetsplug_dir, create_plugin = real_plugin_factory

        create_plugin("deltareal", command_names=["delta-cmd"])

        for cycle in range(2):
            self.config["pluginpath"] = [str(beetsplug_dir)]
            self.config["plugins"] = ["deltareal"]

            load_plugins()
            loaded = [p.name for p in find_plugins()]
            assert "deltareal" in loaded, f"cycle {cycle}"
            assert any(
                c.name == "delta-cmd" for c in beets.plugins.commands()
            ), f"cycle {cycle}"

            self.unload_plugins()
            assert len(list(find_plugins())) == 0, f"cycle {cycle}"

    def test_real_plugin_with_listeners_dispatched(self, real_plugin_factory):
        """Test that a real plugin's listeners actually fire."""
        beetsplug_dir, create_plugin = real_plugin_factory
        tmp_dir = beetsplug_dir.parent

        tracking_file = tmp_dir / "tracker.txt"
        tracking_file.write_text("")

        write_real_plugin(
            beetsplug_dir,
            "epsilonreal",
            command_names=["epsilon-cmd"],
            listeners=[
                (
                    "cli_exit",
                    f"open({str(tracking_file)!r}, 'a').write('fired\\n')",
                )
            ],
        )

        self.config["pluginpath"] = [str(beetsplug_dir)]
        self.config["plugins"] = ["epsilonreal"]

        load_plugins()
        beets.plugins.send("cli_exit")

        content = tracking_file.read_text()
        assert "fired" in content

    def test_real_plugin_nonexistent_raises_nothing(self, real_plugin_factory):
        """Test that requesting a nonexistent plugin does not crash
        (it logs a warning and returns None from _get_plugin).
        """
        beetsplug_dir, _create = real_plugin_factory

        self.config["pluginpath"] = [str(beetsplug_dir)]
        self.config["plugins"] = ["does_not_exist"]

        load_plugins()

        loaded = list(find_plugins())
        assert len(loaded) == 0

    def test_multiple_plugin_dirs_search_order(self, tmp_path: Path):
        """Test that plugins are discovered from all configured pluginpath dirs."""
        dir_a = tmp_path / "plug_a" / "beetsplug"
        dir_b = tmp_path / "plug_b" / "beetsplug"
        for d in (dir_a, dir_b):
            d.mkdir(parents=True)
            (d / "__init__.py").write_text(
                "__path__ = __import__('pkgutil').extend_path(__path__, __name__)\n"
            )

        write_real_plugin(dir_a, "from_a", command_names=["a-cmd"])
        write_real_plugin(dir_b, "from_b", command_names=["b-cmd"])

        self.config["pluginpath"] = [str(dir_a), str(dir_b)]
        self.config["plugins"] = ["from_a", "from_b"]

        load_plugins()

        loaded_names = sorted(p.name for p in find_plugins())
        assert loaded_names == ["from_a", "from_b"]

        cmd_names = [c.name for c in beets.plugins.commands()]
        assert "a-cmd" in cmd_names
        assert "b-cmd" in cmd_names

    def test_real_plugin_module_remains_importable(self, real_plugin_factory):
        """Test that the loaded plugin module remains accessible via import."""
        beetsplug_dir, create_plugin = real_plugin_factory
        create_plugin("zetareal", command_names=["zeta-cmd"])

        self.config["pluginpath"] = [str(beetsplug_dir)]
        self.config["plugins"] = ["zetareal"]

        load_plugins()

        from importlib import import_module

        mod = import_module("beetsplug.zetareal")
        assert hasattr(mod, "ZetarealPlugin")
        assert issubclass(mod.ZetarealPlugin, BeetsPlugin)
