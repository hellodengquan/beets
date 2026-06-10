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


import importlib
import itertools
import logging
import os
import pkgutil
import sys
from typing import ClassVar
from unittest.mock import ANY, Mock, patch

import pytest
from mediafile import MediaFile

from beets import config, plugins, ui
from beets.dbcore import types
from beets.importer import (
    Action,
    ArchiveImportTask,
    SentinelImportTask,
    SingletonImportTask,
)
from beets.library import Item
from beets.test.helper import (
    AutotagStub,
    ImportHelper,
    IOMixin,
    PluginMixin,
    PytestPluginTestHelper,
    TerminalImportMixin,
)
from beets.util import PromptChoice, displayable_path, syspath


class TestPluginRegistration(IOMixin, PytestPluginTestHelper):
    class RatingPlugin(plugins.BeetsPlugin):
        item_types: ClassVar[dict[str, types.Type]] = {
            "rating": types.Float(),
            "multi_value": types.MULTI_VALUE_DSV,
        }

        def __init__(self):
            super().__init__()
            self.register_listener("write", self.on_write)

        @staticmethod
        def on_write(item=None, path=None, tags=None):
            if tags["artist"] == "XXX":
                tags["artist"] = "YYY"

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.register_plugin(self.RatingPlugin)

    def test_field_type_registered(self):
        assert isinstance(Item._types.get("rating"), types.Float)

    def test_duplicate_type(self):
        class DuplicateTypePlugin(plugins.BeetsPlugin):
            item_types: ClassVar[dict[str, types.Type]] = {
                "rating": types.INTEGER
            }

        self.register_plugin(DuplicateTypePlugin)
        with pytest.raises(
            plugins.PluginConflictError, match="already been defined"
        ):
            Item._types

    def test_listener_registered(self):
        self.RatingPlugin()
        item = self.add_item_fixture(artist="XXX")

        item.write()

        assert MediaFile(syspath(item.path)).artist == "YYY"

    def test_multi_value_flex_field_type(self):
        item = Item(path="apath", artist="aaa")
        item.multi_value = ["one", "two", "three"]
        item.add(self.lib)

        out = self.run_with_output("ls", "-f", "$multi_value")
        assert out == "one; two; three\n"


class PytestImportHelper(ImportHelper, PytestPluginTestHelper):
    @pytest.fixture(autouse=True)
    def setup_import_helper(self, setup):
        self.import_media = []
        self.lib.path_formats = [
            ("default", os.path.join("$artist", "$album", "$title")),
            ("singleton:true", os.path.join("singletons", "$title")),
            ("comp:true", os.path.join("compilations", "$album", "$title")),
        ]

        #
        self.prepare_album_for_import(2)


class TestEvents(PytestImportHelper):
    def test_import_task_created(self, caplog):
        self.importer = self.setup_importer(pretend=True)

        with caplog.at_level("DEBUG"):
            self.importer.run()

        # Exactly one event should have been imported (for the album).
        # Sentinels do not get emitted.
        assert caplog.text.count("Sending event: import_task_created") == 1

        logs = [
            msg
            for msg in caplog.messages
            if not msg.startswith("Sending event:")
        ]
        assert logs == [
            f"Album: {displayable_path(os.path.join(self.import_dir, b'album'))}",
            f"  {displayable_path(self.import_media[0].path)}",
            f"  {displayable_path(self.import_media[1].path)}",
        ]

    def test_import_task_created_with_plugin(self, caplog):
        class ToSingletonPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__()

                self.register_listener(
                    "import_task_created", self.import_task_created_event
                )

            def import_task_created_event(self, session, task):
                if (
                    isinstance(task, SingletonImportTask)
                    or isinstance(task, SentinelImportTask)
                    or isinstance(task, ArchiveImportTask)
                ):
                    return task

                new_tasks = []
                for item in task.items:
                    new_tasks.append(SingletonImportTask(task.toppath, item))

                return new_tasks

        to_singleton_plugin = ToSingletonPlugin
        self.register_plugin(to_singleton_plugin)

        self.importer = self.setup_importer(pretend=True)

        with caplog.at_level("DEBUG"):
            self.importer.run()

        # Exactly one event should have been imported (for the album).
        # Sentinels do not get emitted.
        assert caplog.text.count("Sending event: import_task_created") == 1

        logs = [
            msg
            for msg in caplog.messages
            if not msg.startswith("Sending event:")
        ]
        assert logs == [
            f"Singleton: {displayable_path(self.import_media[0].path)}",
            f"Singleton: {displayable_path(self.import_media[1].path)}",
        ]


class TestListeners(PytestPluginTestHelper):
    def test_register(self):
        class DummyPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__()
                self.register_listener("cli_exit", self.dummy)
                self.register_listener("cli_exit", self.dummy)

            def dummy(self):
                pass

        d = DummyPlugin()
        assert DummyPlugin._raw_listeners["cli_exit"] == [d.dummy]

        d2 = DummyPlugin()
        assert DummyPlugin._raw_listeners["cli_exit"] == [d.dummy, d2.dummy]

        d.register_listener("cli_exit", d2.dummy)
        assert DummyPlugin._raw_listeners["cli_exit"] == [d.dummy, d2.dummy]

    def test_events_called(self):
        class DummyPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__()
                self.foo = Mock(__name__="foo")
                self.register_listener("event_foo", self.foo)
                self.bar = Mock(__name__="bar")
                self.register_listener("event_bar", self.bar)

        d = DummyPlugin()

        plugins.send("event")
        d.foo.assert_has_calls([])
        d.bar.assert_has_calls([])

        plugins.send("event_foo", var="tagada")
        d.foo.assert_called_once_with(var="tagada")
        d.bar.assert_has_calls([])

    def test_listener_params(self):
        class DummyPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__()
                for i in itertools.count(1):
                    try:
                        meth = getattr(self, f"dummy{i}")
                    except AttributeError:
                        break
                    self.register_listener(f"event{i}", meth)

            def dummy1(self, foo):
                assert foo == 5

            def dummy2(self, foo=None):
                assert foo == 5

            def dummy3(self):
                # argument cut off
                pass

            def dummy4(self, bar=None):
                # argument cut off
                pass

            def dummy5(self, bar):
                assert not True

            # more complex examples

            def dummy6(self, foo, bar=None):
                assert foo == 5
                assert bar is None

            def dummy7(self, foo, **kwargs):
                assert foo == 5
                assert kwargs == {}

            def dummy8(self, foo, bar, **kwargs):
                assert not True

            def dummy9(self, **kwargs):
                assert kwargs == {"foo": 5}

        DummyPlugin()

        plugins.send("event1", foo=5)
        plugins.send("event2", foo=5)
        plugins.send("event3", foo=5)
        plugins.send("event4", foo=5)

        with pytest.raises(TypeError):
            plugins.send("event5", foo=5)

        plugins.send("event6", foo=5)
        plugins.send("event7", foo=5)

        with pytest.raises(TypeError):
            plugins.send("event8", foo=5)

        plugins.send("event9", foo=5)


class TestPromptChoices(TerminalImportMixin, PytestImportHelper):
    @pytest.fixture(autouse=True)
    def setup_prompt_choice(self, io):
        self.setup_importer()
        self.matcher = AutotagStub(AutotagStub.IDENT).install()
        # keep track of ui.input_option() calls
        self.input_options_patcher = patch(
            "beets.ui.input_options", side_effect=ui.input_options
        )
        self.mock_input_options = self.input_options_patcher.start()
        yield
        self.input_options_patcher.stop()
        self.matcher.restore()

    def test_plugin_choices_in_ui_input_options_album(self):
        """Test the presence of plugin choices on the prompt (album)."""

        class DummyPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__()
                self.register_listener(
                    "before_choose_candidate", self.return_choices
                )

            def return_choices(self, session, task):
                return [
                    PromptChoice("f", "Foo", None),
                    PromptChoice("r", "baR", None),
                ]

        self.register_plugin(DummyPlugin)
        # Default options + extra choices by the plugin ('Foo', 'Bar')
        opts = (
            "Apply",
            "More candidates",
            "Skip",
            "Use as-is",
            "as Tracks",
            "Group albums",
            "Enter search",
            "enter Id",
            "aBort",
            "Foo",
            "baR",
        )

        self.importer.add_choice(Action.SKIP)
        self.importer.run()
        self.mock_input_options.assert_called_once_with(
            opts, default="a", require=ANY
        )

    def test_plugin_choices_in_ui_input_options_singleton(self):
        """Test the presence of plugin choices on the prompt (singleton)."""

        class DummyPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__()
                self.register_listener(
                    "before_choose_candidate", self.return_choices
                )

            def return_choices(self, session, task):
                return [
                    PromptChoice("f", "Foo", None),
                    PromptChoice("r", "baR", None),
                ]

        self.register_plugin(DummyPlugin)
        # Default options + extra choices by the plugin ('Foo', 'Bar')
        opts = (
            "Apply",
            "More candidates",
            "Skip",
            "Use as-is",
            "Enter search",
            "enter Id",
            "aBort",
            "Foo",
            "baR",
        )

        config["import"]["singletons"] = True
        self.importer.add_choice(Action.SKIP)
        self.importer.run()
        self.mock_input_options.assert_called_with(
            opts, default="a", require=ANY
        )

    def test_choices_conflicts(self):
        """Test the short letter conflict solving."""

        class DummyPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__()
                self.register_listener(
                    "before_choose_candidate", self.return_choices
                )

            def return_choices(self, session, task):
                return [
                    PromptChoice("a", "A foo", None),  # dupe
                    PromptChoice("z", "baZ", None),  # ok
                    PromptChoice("z", "Zupe", None),  # dupe
                    PromptChoice("z", "Zoo", None),
                ]  # dupe

        self.register_plugin(DummyPlugin)
        # Default options + not dupe extra choices by the plugin ('baZ')
        opts = (
            "Apply",
            "More candidates",
            "Skip",
            "Use as-is",
            "as Tracks",
            "Group albums",
            "Enter search",
            "enter Id",
            "aBort",
            "baZ",
        )
        self.importer.add_choice(Action.SKIP)
        self.importer.run()
        self.mock_input_options.assert_called_once_with(
            opts, default="a", require=ANY
        )

    def test_plugin_callback(self):
        """Test that plugin callbacks are being called upon user choice."""

        class DummyPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__()
                self.register_listener(
                    "before_choose_candidate", self.return_choices
                )

            def return_choices(self, session, task):
                return [PromptChoice("f", "Foo", self.foo)]

            def foo(self, session, task):
                pass

        self.register_plugin(DummyPlugin)
        # Default options + extra choices by the plugin ('Foo', 'Bar')
        opts = (
            "Apply",
            "More candidates",
            "Skip",
            "Use as-is",
            "as Tracks",
            "Group albums",
            "Enter search",
            "enter Id",
            "aBort",
            "Foo",
        )

        # DummyPlugin.foo() should be called once
        with patch.object(DummyPlugin, "foo", autospec=True) as mock_foo:
            self.io.addinput("f")
            self.io.addinput("n")
            self.importer.run()
            assert mock_foo.call_count == 1

        # input_options should be called twice, as foo() returns None
        assert self.mock_input_options.call_count == 2
        self.mock_input_options.assert_called_with(
            opts, default="a", require=ANY
        )

    def test_plugin_callback_return(self):
        """Test that plugin callbacks that return a value exit the loop."""

        class DummyPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__()
                self.register_listener(
                    "before_choose_candidate", self.return_choices
                )

            def return_choices(self, session, task):
                return [PromptChoice("f", "Foo", self.foo)]

            def foo(self, session, task):
                return Action.SKIP

        self.register_plugin(DummyPlugin)
        # Default options + extra choices by the plugin ('Foo', 'Bar')
        opts = (
            "Apply",
            "More candidates",
            "Skip",
            "Use as-is",
            "as Tracks",
            "Group albums",
            "Enter search",
            "enter Id",
            "aBort",
            "Foo",
        )

        # DummyPlugin.foo() should be called once
        self.io.addinput("f")
        self.importer.run()

        # input_options should be called once, as foo() returns SKIP
        self.mock_input_options.assert_called_once_with(
            opts, default="a", require=ANY
        )


def get_available_plugins():
    """Get all available plugins in the beetsplug namespace."""
    namespace_pkg = importlib.import_module("beetsplug")

    return [
        m.name
        for m in pkgutil.iter_modules(namespace_pkg.__path__)
        if not m.name.startswith("_")
    ]


class TestImportPlugin(PluginMixin):
    @pytest.fixture(params=get_available_plugins())
    def plugin_name(self, request):
        """Fixture to provide the name of each available plugin."""
        name = request.param

        # skip gstreamer plugins on windows
        gstreamer_plugins = {"bpd", "replaygain"}
        if sys.platform == "win32" and name in gstreamer_plugins:
            pytest.skip(f"GStreamer is not available on Windows: {name}")

        return name

    def unload_plugins(self):
        """Unimport plugins before each test to avoid conflicts."""
        super().unload_plugins()
        for mod in list(sys.modules):
            if mod.startswith("beetsplug."):
                del sys.modules[mod]

    @pytest.fixture(autouse=True)
    def cleanup(self):
        """Ensure plugins are unimported before and after each test."""
        self.unload_plugins()
        yield
        self.unload_plugins()

    @pytest.mark.skipif(
        os.environ.get("GITHUB_ACTIONS") != "true",
        reason=(
            "Requires all dependencies to be installed, which we can't"
            " guarantee in the local environment."
        ),
    )
    def test_import_plugin(self, caplog, plugin_name):
        """Test that a plugin is importable without an error."""
        caplog.set_level(logging.WARNING)
        self.load_plugins(plugin_name)

        assert "PluginImportError" not in caplog.text, (
            f"Plugin '{plugin_name}' has issues during import."
        )


class TestDeprecationCopy:
    # TODO: remove this test in Beets 3.0.0
    def test_legacy_metadata_plugin_deprecation(self):
        """Test that a MetadataSourcePlugin with 'legacy' data_source
        raises a deprecation warning and all function and properties are
        copied from the base class.
        """
        with pytest.warns(DeprecationWarning, match="LegacyMetadataPlugin"):

            class LegacyMetadataPlugin(plugins.BeetsPlugin):
                data_source = "legacy"

        # Assert all methods are present
        assert hasattr(LegacyMetadataPlugin, "albums_for_ids")
        assert hasattr(LegacyMetadataPlugin, "tracks_for_ids")
        assert hasattr(LegacyMetadataPlugin, "data_source_mismatch_penalty")
        assert hasattr(LegacyMetadataPlugin, "_extract_id")
        assert hasattr(LegacyMetadataPlugin, "get_artist")


class TestMusicBrainzPluginLoading:
    @pytest.fixture(autouse=True)
    def config(self):
        _config = config
        _config.sources = []
        _config.read(user=False, defaults=True)
        return _config

    def test_default(self):
        assert "musicbrainz" in plugins.get_plugin_names()

    def test_other_plugin_enabled(self, config):
        config["plugins"] = ["anything"]

        assert "musicbrainz" not in plugins.get_plugin_names()

    def test_deprecated_enabled(self, config, caplog):
        config["plugins"] = ["anything"]
        config["musicbrainz"]["enabled"] = True

        assert "musicbrainz" in plugins.get_plugin_names()
        assert (
            "musicbrainz.enabled' configuration option is deprecated"
            in caplog.text
        )

    def test_deprecated_disabled(self, config, caplog):
        config["musicbrainz"]["enabled"] = False

        assert "musicbrainz" not in plugins.get_plugin_names()
        assert (
            "musicbrainz.enabled' configuration option is deprecated"
            in caplog.text
        )


class TestMultiplePluginsEnabled(PytestPluginTestHelper):
    """Test that multiple plugins can be enabled simultaneously and
    their commands, listeners, and template functions coexist properly.
    """

    def test_multiple_plugins_commands_registered(self):
        """Test that commands from multiple plugins are all registered."""

        class PluginA(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("plugina")

            def commands(self):
                cmd_a = ui.Subcommand(
                    "cmd-a", help="Command from PluginA"
                )

                def func_a(lib, opts, args):
                    pass

                cmd_a.func = func_a
                return [cmd_a]

        class PluginB(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("pluginb")

            def commands(self):
                cmd_b = ui.Subcommand(
                    "cmd-b", help="Command from PluginB"
                )

                def func_b(lib, opts, args):
                    pass

                cmd_b.func = func_b
                return [cmd_b]

        class PluginC(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("pluginc")

            def commands(self):
                cmd_c1 = ui.Subcommand(
                    "cmd-c1", help="First command from PluginC"
                )
                cmd_c2 = ui.Subcommand(
                    "cmd-c2", help="Second command from PluginC"
                )

                def func_c(lib, opts, args):
                    pass

                cmd_c1.func = func_c
                cmd_c2.func = func_c
                return [cmd_c1, cmd_c2]

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

    def test_multiple_plugins_listeners_independent(self):
        """Test that listeners from different plugins are independent."""
        call_order = []

        class PluginAlpha(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("pluginalpha")
                self.register_listener("cli_exit", self.on_exit)

            def on_exit(self):
                call_order.append("alpha")

        class PluginBeta(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("pluginbeta")
                self.register_listener("cli_exit", self.on_exit)

            def on_exit(self):
                call_order.append("beta")

        self.register_plugin(PluginAlpha)
        self.register_plugin(PluginBeta)

        plugins.send("cli_exit")

        assert "alpha" in call_order
        assert "beta" in call_order
        assert len(call_order) == 2

    def test_multiple_plugins_instance_count(self):
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

    def test_multiple_plugins_find_plugins_returns_all(self):
        """Test that find_plugins() returns all registered plugins."""

        class PluginX(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("pluginx")

        class PluginY(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("pluginy")

        class PluginZ(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("pluginz")

        self.register_plugin(PluginX)
        self.register_plugin(PluginY)
        self.register_plugin(PluginZ)

        found_plugins = list(plugins.find_plugins())
        plugin_names = [p.name for p in found_plugins]

        assert len(found_plugins) == 3
        assert "pluginx" in plugin_names
        assert "pluginy" in plugin_names
        assert "pluginz" in plugin_names

    def test_multiple_plugins_template_fields_no_conflict(self):
        """Test that template fields from different plugins coexist."""

        class PluginFieldsA(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("pluginfieldsa")
                self.template_fields["custom_a"] = lambda item: "value_a"

        class PluginFieldsB(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("pluginfieldsb")
                self.template_fields["custom_b"] = lambda item: "value_b"

        self.register_plugin(PluginFieldsA)
        self.register_plugin(PluginFieldsB)

        getters = plugins.item_field_getters()
        assert "custom_a" in getters
        assert "custom_b" in getters


class TestPluginDisableCleanup(PytestPluginTestHelper):
    """Test that disabling plugins properly cleans up commands,
    listeners, and other plugin-registered resources.
    """

    def test_unload_plugins_clears_instances(self):
        """Test that unload_plugins clears the plugin instances."""

        class CleanupPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("cleanupplugin")

            def commands(self):
                cmd = ui.Subcommand("cleanup-cmd")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(CleanupPlugin)
        assert len(list(plugins.find_plugins())) == 1

        self.unload_plugins()
        assert len(list(plugins.find_plugins())) == 0

    def test_unload_plugins_clears_commands(self):
        """Test that commands are cleared after unloading plugins."""

        class CommandPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("commandplugin")

            def commands(self):
                cmd = ui.Subcommand("my-temp-cmd")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

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

    def test_unload_plugins_isolates_between_tests(self):
        """Test that state from one test doesn't leak to next."""

        class LeakTestPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("leaktestplugin")

            def commands(self):
                cmd = ui.Subcommand("leak-cmd")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(LeakTestPlugin)
        assert len(plugins.commands()) == 1

        self.unload_plugins()

        assert len(plugins.commands()) == 0
        assert len(list(plugins.find_plugins())) == 0

    def test_partial_plugin_disable(self):
        """Test behavior when some plugins are disabled via config."""

        class PluginKeep(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("pluginkeep")

            def commands(self):
                cmd = ui.Subcommand("keep-cmd")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        class PluginDisable(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("plugindisable")

            def commands(self):
                cmd = ui.Subcommand("disable-cmd")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

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


class TestPluginConfigReload(PytestPluginTestHelper):
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
        import sys

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

    def test_reload_clears_and_reloads_listeners(self):
        """Test that a full clear+reload properly resets listeners."""
        call_log = []

        class ReloadPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("reloadplugin")
                self.register_listener("cli_exit", self.on_exit)

            def on_exit(self):
                call_log.append("reloadplugin")

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

    def test_multiple_config_reload_cycles(self):
        """Test multiple cycles of load/unload."""

        class CyclePlugin(plugins.BeetsPlugin):
            init_count = 0

            def __init__(self):
                super().__init__("cycleplugin")
                CyclePlugin.init_count += 1

            def commands(self):
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


class TestDuplicateCommandName(PytestPluginTestHelper):
    """Test behavior when multiple plugins register commands with
    the same name.
    """

    def test_duplicate_commands_both_returned(self):
        """Test that duplicate-named commands are both returned by
        plugins.commands() (last one wins in parser).
        """

        class PluginFirst(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("pluginfirst")

            def commands(self):
                cmd = ui.Subcommand("duplicate-cmd", help="From first plugin")
                cmd.func = lambda lib, opts, args: "first"
                return [cmd]

        class PluginSecond(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("pluginsecond")

            def commands(self):
                cmd = ui.Subcommand("duplicate-cmd", help="From second plugin")
                cmd.func = lambda lib, opts, args: "second"
                return [cmd]

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

    def test_after_unload_no_conflict_remains(self):
        """Test that after unloading plugins, conflicting commands
        are removed.
        """

        class ConflictPluginA(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("conflicta")

            def commands(self):
                cmd = ui.Subcommand("shared-cmd")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        class ConflictPluginB(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("conflictb")

            def commands(self):
                cmd = ui.Subcommand("shared-cmd")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(ConflictPluginA)
        self.register_plugin(ConflictPluginB)

        shared_cmds = [
            c for c in plugins.commands() if c.name == "shared-cmd"
        ]
        assert len(shared_cmds) == 2

        self.unload_plugins()
        assert len(plugins.commands()) == 0


class TestHookCallOrder(PytestPluginTestHelper):
    """Test that event hooks are called in a predictable order."""

    def test_single_plugin_listener_order_preserved(self):
        """Test that listeners registered in order are called in order."""
        call_order = []

        class OrderedPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("orderedplugin")
                self.register_listener("cli_exit", self.first)
                self.register_listener("cli_exit", self.second)
                self.register_listener("cli_exit", self.third)

            def first(self):
                call_order.append("first")

            def second(self):
                call_order.append("second")

            def third(self):
                call_order.append("third")

        self.register_plugin(OrderedPlugin)
        plugins.send("cli_exit")

        assert call_order == ["first", "second", "third"]

    def test_multiple_plugins_listener_order(self):
        """Test listeners from different plugins called in registration order."""
        call_order = []

        class PluginA(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("hook_a")
                self.register_listener("cli_exit", self.on_exit)

            def on_exit(self):
                call_order.append("PluginA")

        class PluginB(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("hook_b")
                self.register_listener("cli_exit", self.on_exit)

            def on_exit(self):
                call_order.append("PluginB")

        class PluginC(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("hook_c")
                self.register_listener("cli_exit", self.on_exit)

            def on_exit(self):
                call_order.append("PluginC")

        self.register_plugin(PluginA)
        self.register_plugin(PluginB)
        self.register_plugin(PluginC)

        plugins.send("cli_exit")

        assert call_order == ["PluginA", "PluginB", "PluginC"]

    def test_listener_duplicate_function_not_added_twice(self):
        """Test that the same function registered twice is only called once."""
        call_count = [0]

        class DupPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("dupplugin")
                self.register_listener("cli_exit", self.my_handler)
                self.register_listener("cli_exit", self.my_handler)

            def my_handler(self):
                call_count[0] += 1

        self.register_plugin(DupPlugin)
        plugins.send("cli_exit")

        assert call_count[0] == 1

    def test_listener_results_collected(self):
        """Test that non-None return values are collected in order."""

        class ResultPluginA(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("result_a")
                self.register_listener("write", self.on_write)

            def on_write(self, **kwargs):
                return "result_from_a"

        class ResultPluginB(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("result_b")
                self.register_listener("write", self.on_write)

            def on_write(self, **kwargs):
                return None

        class ResultPluginC(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("result_c")
                self.register_listener("write", self.on_write)

            def on_write(self, **kwargs):
                return "result_from_c"

        self.register_plugin(ResultPluginA)
        self.register_plugin(ResultPluginB)
        self.register_plugin(ResultPluginC)

        results = plugins.send("write")

        assert results == ["result_from_a", "result_from_c"]

    def test_interleaved_events_order(self):
        """Test ordering holds across different event types."""
        event_log = []

        class InterleavePlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("interleaveplugin")
                self.register_listener("cli_exit", self.exit_handler)
                self.register_listener("library_opened", self.lib_handler)
                self.register_listener("pluginload", self.load_handler)

            def exit_handler(self):
                event_log.append(("cli_exit", 1))

            def lib_handler(self, lib=None):
                event_log.append(("library_opened", 1))

            def load_handler(self):
                event_log.append(("pluginload", 1))

        self.register_plugin(InterleavePlugin)

        plugins.send("library_opened", lib=None)
        plugins.send("cli_exit")
        plugins.send("pluginload")

        assert event_log == [
            ("library_opened", 1),
            ("cli_exit", 1),
            ("pluginload", 1),
        ]

    def test_multiple_instances_same_plugin_order(self):
        """Test multiple instances of the same plugin class."""
        call_log = []

        class MultiInstancePlugin(plugins.BeetsPlugin):
            def __init__(self, identifier):
                super().__init__(f"multi_{identifier}")
                self.identifier = identifier
                self.register_listener("cli_exit", self.handler)

            def handler(self):
                call_log.append(self.identifier)

        plugins._instances.append(MultiInstancePlugin("first_inst"))
        plugins._instances.append(MultiInstancePlugin("second_inst"))
        plugins._instances.append(MultiInstancePlugin("third_inst"))

        plugins.send("cli_exit")

        assert call_log == ["first_inst", "second_inst", "third_inst"]

    def test_hook_order_preserved_after_send(self):
        """Test that listener order is preserved across multiple sends."""
        call_logs = []

        class StablePluginA(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("stable_a")
                self.register_listener("cli_exit", self.h)

            def h(self):
                return "A"

        class StablePluginB(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("stable_b")
                self.register_listener("cli_exit", self.h)

            def h(self):
                return "B"

        self.register_plugin(StablePluginA)
        self.register_plugin(StablePluginB)

        for _ in range(5):
            call_logs.append(plugins.send("cli_exit"))

        for log in call_logs:
            assert log == ["A", "B"]


class TestPluginGlobalStateIsolation(PytestPluginTestHelper):
    """Verify that plugin global state is properly isolated between tests.
    This class serves as a regression test to ensure the cleanup mechanism
    in PytestPluginTestHelper.unload_plugins works correctly.
    """

    def test_isolation_state_cleared_1(self):
        """First isolation test - registers plugins."""

        class IsoPlugin1(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("iso_plugin_1")

            def commands(self):
                cmd = ui.Subcommand("iso-cmd-1")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(IsoPlugin1)
        assert len(list(plugins.find_plugins())) == 1
        assert len(plugins.commands()) == 1

    def test_isolation_state_cleared_2(self):
        """Second isolation test - should NOT see state from test 1."""
        assert len(list(plugins.find_plugins())) == 0
        assert len(plugins.commands()) == 0
        assert len(plugins.BeetsPlugin.listeners["cli_exit"]) == 0
        assert len(plugins.BeetsPlugin._raw_listeners["cli_exit"]) == 0

        class IsoPlugin2(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("iso_plugin_2")

            def commands(self):
                cmd = ui.Subcommand("iso-cmd-2")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(IsoPlugin2)
        assert len(list(plugins.find_plugins())) == 1
        assert len(plugins.commands()) == 1
        assert any(c.name == "iso-cmd-2" for c in plugins.commands())

    def test_isolation_listeners_cleared_1(self):
        """First listener isolation test."""
        self.shared_flag = [False]

        class IsoListener1(plugins.BeetsPlugin):
            def __init__(self, outer):
                super().__init__("iso_listener_1")
                self._outer = outer
                self.register_listener("cli_exit", self.handler)

            def handler(self):
                self._outer.shared_flag[0] = True

        plugins._instances.append(IsoListener1(self))
        plugins.send("cli_exit")
        assert self.shared_flag[0] is True

    def test_isolation_listeners_cleared_2(self):
        """Second listener isolation test."""
        flag = [False]

        class IsoListener2(plugins.BeetsPlugin):
            def __init__(self, outer):
                super().__init__("iso_listener_2")
                self._outer = outer
                self.register_listener("cli_exit", self.handler)

            def handler(self):
                self._outer[0] = True

        plugins.send("cli_exit")
        assert flag[0] is False, "Listener from previous test leaked!"

        plugins._instances.append(IsoListener2(flag))
        plugins.send("cli_exit")
        assert flag[0] is True

