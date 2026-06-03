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


class TestPluginCommandRegistration(PytestPluginTestHelper):
    """Tests for the unified plugin command registration system."""

    def test_duplicate_command_between_plugins_warning(self, caplog):
        """Test that duplicate commands between plugins produce a warning."""

        class PluginA(plugins.BeetsPlugin):
            def commands(self):
                cmd = ui.Subcommand("testcmd", help="test command A")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        class PluginB(plugins.BeetsPlugin):
            def commands(self):
                cmd = ui.Subcommand("testcmd", help="test command B")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(PluginA)
        self.register_plugin(PluginB)

        with caplog.at_level(logging.WARNING):
            registered = plugins.register_plugin_commands(fail_on_duplicate=False)

        assert len(registered) == 1
        assert "already defined by plugin" in caplog.text

    def test_duplicate_command_between_plugins_error(self):
        """Test that duplicate commands between plugins raise an error when fail_on_duplicate is True."""

        class PluginA(plugins.BeetsPlugin):
            def commands(self):
                cmd = ui.Subcommand("testcmd", help="test command A")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        class PluginB(plugins.BeetsPlugin):
            def commands(self):
                cmd = ui.Subcommand("testcmd", help="test command B")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(PluginA)
        self.register_plugin(PluginB)

        with pytest.raises(
            plugins.DuplicateCommandError, match="already registered by plugin"
        ):
            plugins.register_plugin_commands(fail_on_duplicate=True)

    def test_duplicate_alias_between_plugins_warning(self, caplog):
        """Test that duplicate aliases between plugins produce a warning."""

        class PluginA(plugins.BeetsPlugin):
            def commands(self):
                cmd = ui.Subcommand("cmda", help="command A", aliases=["tc"])
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        class PluginB(plugins.BeetsPlugin):
            def commands(self):
                cmd = ui.Subcommand("cmdb", help="command B", aliases=["tc"])
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(PluginA)
        self.register_plugin(PluginB)

        with caplog.at_level(logging.WARNING):
            registered = plugins.register_plugin_commands(fail_on_duplicate=False)

        assert len(registered) == 1
        assert "already defined by plugin" in caplog.text

    def test_duplicate_with_builtin_command_warning(self, caplog):
        """Test that commands conflicting with built-in commands produce a warning."""

        class TestPlugin(plugins.BeetsPlugin):
            def commands(self):
                cmd = ui.Subcommand("import", help="override import")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(TestPlugin)

        builtin_cmd = ui.Subcommand("import", help="builtin import")
        builtin_cmd.func = lambda lib, opts, args: None

        with caplog.at_level(logging.WARNING):
            registered = plugins.register_plugin_commands(
                builtin_commands=[builtin_cmd], fail_on_duplicate=False
            )

        assert len(registered) == 0
        assert "conflicts with a built-in beets command" in caplog.text

    def test_get_registered_commands(self):
        """Test that get_registered_commands returns the command registry."""

        class TestPlugin(plugins.BeetsPlugin):
            def commands(self):
                cmd = ui.Subcommand("mycmd", help="my command", aliases=["mc"])
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(TestPlugin)
        plugins.register_plugin_commands(fail_on_duplicate=False)

        registry = plugins.get_registered_commands()
        assert "mycmd" in registry
        assert "mc" in registry
        assert registry["mycmd"] == "test_plugins"
        assert registry["mc"] == "test_plugins"


class TestDisabledPluginsNotification(PytestPluginTestHelper):
    """Tests for disabled plugin notification system."""

    @pytest.fixture(autouse=True)
    def _reset_state(self):
        """Reset plugin state before each test."""
        plugins._instances.clear()
        plugins._disabled_plugins.clear()
        yield
        plugins._instances.clear()
        plugins._disabled_plugins.clear()

    def test_disabled_plugin_logged(self, caplog):
        """Test that disabled plugins are logged."""
        self.config["plugins"] = ["test_plugin"]
        self.config["disabled_plugins"] = ["test_plugin"]

        with caplog.at_level(logging.INFO):
            plugins.load_plugins()

        assert "Plugin 'test_plugin' is disabled" in caplog.text

    def test_get_disabled_plugins_returns_list(self):
        """Test that get_disabled_plugins returns a list of notifications."""
        self.config["plugins"] = ["test_plugin"]
        self.config["disabled_plugins"] = ["test_plugin"]

        plugins.load_plugins()

        disabled = plugins.get_disabled_plugins()
        assert isinstance(disabled, list)
        assert len(disabled) >= 1

    def test_disabled_plugin_notification_has_reason(self):
        """Test that disabled plugin notifications have a reason."""
        self.config["plugins"] = ["test_plugin"]
        self.config["disabled_plugins"] = ["test_plugin"]

        plugins.load_plugins()

        disabled = plugins.get_disabled_plugins()
        for notification in disabled:
            assert hasattr(notification, "name")
            assert hasattr(notification, "reason")
            assert notification.reason is not None


class TestCommandConflictTracking(PytestPluginTestHelper):
    """Tests for the CommandConflict data class and get_command_conflicts."""

    def test_get_command_conflicts_empty_when_no_conflicts(self):
        """Test that get_command_conflicts returns empty when there are none."""

        class TestPlugin(plugins.BeetsPlugin):
            def commands(self):
                cmd = ui.Subcommand("uniquecmd", help="unique")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(TestPlugin)
        plugins.register_plugin_commands(fail_on_duplicate=False)

        assert plugins.get_command_conflicts() == []

    def test_get_command_conflicts_captures_plugin_duplicate(self):
        """Test that get_command_conflicts captures conflicts between plugins."""

        class PluginA(plugins.BeetsPlugin):
            def commands(self):
                cmd = ui.Subcommand("dupcmd", help="a")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        class PluginB(plugins.BeetsPlugin):
            def commands(self):
                cmd = ui.Subcommand("dupcmd", help="b")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(PluginA)
        self.register_plugin(PluginB)
        plugins.register_plugin_commands(fail_on_duplicate=False)

        conflicts = plugins.get_command_conflicts()
        assert len(conflicts) == 1
        assert conflicts[0].command_name == "dupcmd"
        assert conflicts[0].is_builtin is False
        assert conflicts[0].existing_plugin is not None

    def test_get_command_conflicts_captures_builtin_duplicate(self):
        """Test that get_command_conflicts captures conflicts with builtins."""

        class TestPlugin(plugins.BeetsPlugin):
            def commands(self):
                cmd = ui.Subcommand("import", help="override")
                cmd.func = lambda lib, opts, args: None
                return [cmd]

        self.register_plugin(TestPlugin)

        builtin_cmd = ui.Subcommand("import", help="builtin import")
        builtin_cmd.func = lambda lib, opts, args: None
        plugins.register_plugin_commands(
            builtin_commands=[builtin_cmd], fail_on_duplicate=False
        )

        conflicts = plugins.get_command_conflicts()
        assert len(conflicts) == 1
        assert conflicts[0].command_name == "import"
        assert conflicts[0].is_builtin is True

    def test_command_conflict_str_builtin(self):
        """Test CommandConflict __str__ for builtin conflict."""
        conflict = plugins.CommandConflict(
            "import", "myplug", is_builtin=True
        )
        assert "conflicts with a built-in beets command" in str(conflict)

    def test_command_conflict_str_plugin(self):
        """Test CommandConflict __str__ for plugin conflict."""
        conflict = plugins.CommandConflict(
            "cmd", "plug_b", existing_plugin="plug_a"
        )
        assert "conflicts with plugin 'plug_a'" in str(conflict)


class TestPrintPluginNotices(PytestPluginTestHelper):
    """Tests for _print_plugin_notices CLI-visible output."""

    @pytest.fixture(autouse=True)
    def _reset_state(self):
        """Reset plugin state before each test."""
        plugins._instances.clear()
        plugins._disabled_plugins.clear()
        plugins._command_conflicts.clear()
        yield
        plugins._instances.clear()
        plugins._disabled_plugins.clear()
        plugins._command_conflicts.clear()

    def test_print_plugin_notices_disabled_plugins(self, capsys):
        """Test that _print_plugin_notices prints disabled plugins to stderr."""
        from beets.ui import _print_plugin_notices

        plugins._disabled_plugins.append(
            plugins.DisabledPluginNotification(
                "myplug", "explicitly disabled via configuration"
            )
        )

        _print_plugin_notices()

        captured = capsys.readouterr()
        assert "Disabled plugins:" in captured.err
        assert "myplug" in captured.err
        assert "explicitly disabled via configuration" in captured.err

    def test_print_plugin_notices_command_conflicts(self, capsys):
        """Test that _print_plugin_notices prints conflicts to stderr."""
        from beets.ui import _print_plugin_notices

        plugins._command_conflicts.append(
            plugins.CommandConflict("dupcmd", "plug_b", existing_plugin="plug_a")
        )

        _print_plugin_notices()

        captured = capsys.readouterr()
        assert "Command conflicts detected:" in captured.err
        assert "dupcmd" in captured.err
        assert "plug_a" in captured.err

    def test_print_plugin_notices_no_output_when_all_ok(self, capsys):
        """Test that _print_plugin_notices produces no output when there are no issues."""
        from beets.ui import _print_plugin_notices

        _print_plugin_notices()

        captured = capsys.readouterr()
        assert captured.err == ""

    def test_print_plugin_notices_both(self, capsys):
        """Test that _print_plugin_notices prints both sections when both exist."""
        from beets.ui import _print_plugin_notices

        plugins._disabled_plugins.append(
            plugins.DisabledPluginNotification("plug1", "disabled for test")
        )
        plugins._command_conflicts.append(
            plugins.CommandConflict("cmd1", "plug2", existing_plugin="plug3")
        )

        _print_plugin_notices()

        captured = capsys.readouterr()
        assert "Disabled plugins:" in captured.err
        assert "Command conflicts detected:" in captured.err
        assert "plug1" in captured.err
        assert "cmd1" in captured.err
