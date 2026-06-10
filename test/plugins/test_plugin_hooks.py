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

from beets import plugins

from test.plugins.conftest import PluginLifecycleTestHelper


class TestHookCallOrder(PluginLifecycleTestHelper):
    """Test that event hooks are called in a predictable order."""

    def test_single_plugin_listener_order_preserved(self, call_log):
        """Test that listeners registered in order are called in order."""

        class OrderedPlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("orderedplugin")
                self.register_listener("cli_exit", self.first)
                self.register_listener("cli_exit", self.second)
                self.register_listener("cli_exit", self.third)

            def first(self):
                call_log.append("first")

            def second(self):
                call_log.append("second")

            def third(self):
                call_log.append("third")

        self.register_plugin(OrderedPlugin)
        plugins.send("cli_exit")

        assert call_log == ["first", "second", "third"]

    def test_multiple_plugins_listener_order(self, call_log):
        """Test listeners from different plugins called in registration order."""

        def make_handler(name):
            def _h():
                call_log.append(name)

            return _h

        PluginA = self.make_listener_plugin(
            "hook_a", events=[("cli_exit", make_handler("PluginA"))]
        )
        PluginB = self.make_listener_plugin(
            "hook_b", events=[("cli_exit", make_handler("PluginB"))]
        )
        PluginC = self.make_listener_plugin(
            "hook_c", events=[("cli_exit", make_handler("PluginC"))]
        )

        self.register_plugin(PluginA)
        self.register_plugin(PluginB)
        self.register_plugin(PluginC)

        plugins.send("cli_exit")

        assert call_log == ["PluginA", "PluginB", "PluginC"]

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
