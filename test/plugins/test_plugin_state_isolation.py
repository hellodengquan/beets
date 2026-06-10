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


class TestPluginGlobalStateIsolation(PluginLifecycleTestHelper):
    """Verify that plugin global state is properly isolated between tests.
    This class serves as a regression test to ensure the cleanup mechanism
    in PytestPluginTestHelper.unload_plugins works correctly.

    Each test method pair (1/2) verifies that state registered in the
    "X_cleared_1" method does not leak into the "X_cleared_2" method.
    """

    def test_isolation_state_cleared_1(self, plugin_factory):
        """First isolation test - registers plugins."""
        IsoPlugin1 = plugin_factory.make_command_plugin(
            "iso_plugin_1", command_names=["iso-cmd-1"]
        )

        self.register_plugin(IsoPlugin1)
        assert len(list(plugins.find_plugins())) == 1
        assert len(plugins.commands()) == 1

    def test_isolation_state_cleared_2(self, plugin_factory):
        """Second isolation test - should NOT see state from test 1."""
        assert len(list(plugins.find_plugins())) == 0
        assert len(plugins.commands()) == 0
        assert len(plugins.BeetsPlugin.listeners["cli_exit"]) == 0
        assert len(plugins.BeetsPlugin._raw_listeners["cli_exit"]) == 0

        IsoPlugin2 = plugin_factory.make_command_plugin(
            "iso_plugin_2", command_names=["iso-cmd-2"]
        )
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

    def test_isolation_template_fields_cleared_1(self, plugin_factory):
        """First template field isolation test."""
        FieldsPluginA = plugin_factory.make_template_field_plugin(
            "iso_fields_a", fields={"iso_field_a": lambda item: "A"}
        )
        self.register_plugin(FieldsPluginA)

        getters = plugins.item_field_getters()
        assert "iso_field_a" in getters

    def test_isolation_template_fields_cleared_2(self, plugin_factory):
        """Second template field isolation test - should NOT see field from test 1."""
        getters = plugins.item_field_getters()
        assert "iso_field_a" not in getters

        FieldsPluginB = plugin_factory.make_template_field_plugin(
            "iso_fields_b", fields={"iso_field_b": lambda item: "B"}
        )
        self.register_plugin(FieldsPluginB)

        getters = plugins.item_field_getters()
        assert "iso_field_b" in getters
        assert "iso_field_a" not in getters

    def test_isolation_instances_cleared_1(self):
        """First instances isolation test."""

        class IsoInstancePlugin(plugins.BeetsPlugin):
            def __init__(self):
                super().__init__("iso_instance_plugin")

        self.register_plugin(IsoInstancePlugin)
        names = [p.name for p in plugins.find_plugins()]
        assert "iso_instance_plugin" in names

    def test_isolation_instances_cleared_2(self):
        """Second instances isolation test."""
        names = [p.name for p in plugins.find_plugins()]
        assert "iso_instance_plugin" not in names
        assert len(list(plugins.find_plugins())) == 0
