from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import pytest
import requests

import beets.plugins
from beets import ui
from beets.test.helper import PytestPluginTestHelper

if TYPE_CHECKING:
    from requests_mock import Mocker


@pytest.fixture
def requests_mock(requests_mock, monkeypatch) -> Mocker:
    """Use plain session wherever MB requests are mocked.

    This avoids rate limiting requests to speed up tests.
    """
    monkeypatch.setattr(
        "beetsplug._utils.musicbrainz.MusicBrainzAPI.create_session",
        lambda _: requests.Session(),
    )
    return requests_mock


def make_command_plugin(
    plugin_name: str,
    command_names: list[str] | None = None,
    command_funcs: dict[str, Callable] | None = None,
) -> type[beets.plugins.BeetsPlugin]:
    """Factory to create a plugin class that registers CLI commands.

    Args:
        plugin_name: The name to use for the plugin.
        command_names: List of command names to register. Defaults to
            [f"{plugin_name}-cmd"].
        command_funcs: Optional mapping of command name to callable.

    Returns:
        A BeetsPlugin subclass ready for registration.
    """
    command_names = command_names or [f"{plugin_name}-cmd"]
    command_funcs = command_funcs or {}

    class _CmdPlugin(beets.plugins.BeetsPlugin):
        def __init__(self):
            super().__init__(plugin_name)

        def commands(self):
            results = []
            for cmd_name in command_names:
                cmd = ui.Subcommand(
                    cmd_name, help=f"Command from {plugin_name}"
                )
                cmd.func = command_funcs.get(
                    cmd_name,
                    (lambda n: lambda lib, opts, args: None)(cmd_name),
                )
                results.append(cmd)
            return results

    return _CmdPlugin


def make_listener_plugin(
    plugin_name: str,
    events: list[tuple[str, Callable]] | None = None,
    call_log_ref: list | None = None,
) -> type[beets.plugins.BeetsPlugin]:
    """Factory to create a plugin class with event listeners.

    Args:
        plugin_name: The name to use for the plugin.
        events: List of (event_name, handler) tuples. If None, a default
            "cli_exit" listener is created that appends plugin_name to
            call_log_ref.
        call_log_ref: Shared list to record listener invocations.

    Returns:
        A BeetsPlugin subclass ready for registration.
    """
    if events is None:
        log = call_log_ref if call_log_ref is not None else []

        def default_handler(_name=plugin_name, _log=log):
            _log.append(_name)

        events = [("cli_exit", default_handler)]

    class _ListenerPlugin(beets.plugins.BeetsPlugin):
        def __init__(self):
            super().__init__(plugin_name)
            for event_name, handler in events:
                self.register_listener(event_name, handler)

    return _ListenerPlugin


def make_template_field_plugin(
    plugin_name: str,
    fields: dict[str, Callable] | None = None,
) -> type[beets.plugins.BeetsPlugin]:
    """Factory to create a plugin class that registers template fields.

    Args:
        plugin_name: The name to use for the plugin.
        fields: Mapping of field name to getter function.

    Returns:
        A BeetsPlugin subclass ready for registration.
    """
    fields = fields or {f"{plugin_name}_field": lambda item: plugin_name}

    class _FieldPlugin(beets.plugins.BeetsPlugin):
        def __init__(self):
            super().__init__(plugin_name)
            for name, getter in fields.items():
                self.template_fields[name] = getter

    return _FieldPlugin


@pytest.fixture
def plugin_factory():
    """Fixture that exposes all plugin factory helpers."""

    class _Factories:
        make_command_plugin = staticmethod(make_command_plugin)
        make_listener_plugin = staticmethod(make_listener_plugin)
        make_template_field_plugin = staticmethod(make_template_field_plugin)

    return _Factories


@pytest.fixture
def call_log():
    """A shared list for tracking listener call order."""
    return []


@pytest.fixture
def temp_plugin_dir(tmp_path: Path) -> tuple[Path, Path]:
    """Create a temporary directory and beetsplug namespace for
    loading real plugin packages.

    Per beets/plugins.py:get_plugin_names() conventions, pluginpath
    config entries are added both to ``beetsplug.__path__`` and
    ``sys.path``.  Therefore ``config["pluginpath"]`` should point
    to the *beetsplug/* directory itself (not its parent).  This
    fixture returns both the plugin root and the beetsplug namespace
    directory for convenience.

    Returns:
        Tuple of (beetsplug_dir, beetsplug_dir).  The first element
        is what should be configured in ``config["pluginpath"]``.
    """
    ext_plugins_root = tmp_path / "extplugins"
    ext_plugins_root.mkdir()

    beetsplug_ns = ext_plugins_root / "beetsplug"
    beetsplug_ns.mkdir()

    ns_init = beetsplug_ns / "__init__.py"
    ns_init.write_text(
        "__path__ = __import__('pkgutil').extend_path(__path__, __name__)\n"
    )

    return beetsplug_ns, beetsplug_ns


def write_real_plugin(
    beetsplug_dir: Path,
    plugin_name: str,
    command_names: list[str] | None = None,
    listeners: list[tuple[str, str]] | None = None,
) -> Path:
    """Write a real plugin module into a beetsplug namespace directory.

    The plugin file is placed at beetsplug_dir/{plugin_name}.py.

    Args:
        beetsplug_dir: The directory that is configured as pluginpath
            (should be a beetsplug namespace directory containing an
            ``__init__.py``).
        plugin_name: Name of the plugin (module name).
        command_names: List of command names the plugin exposes.
        listeners: List of (event_name, handler_body_source) tuples.

    Returns:
        Path to the written plugin .py file.
    """
    command_names = command_names or [f"{plugin_name}_cmd"]
    listeners = listeners or []

    beetsplug_dir.mkdir(exist_ok=True)
    if not (beetsplug_dir / "__init__.py").exists():
        (beetsplug_dir / "__init__.py").write_text(
            "__path__ = __import__('pkgutil').extend_path(__path__, __name__)\n"
        )

    plugin_file = beetsplug_dir / f"{plugin_name}.py"

    listener_registrations = ""
    listener_defs = ""
    for idx, (ev_name, body) in enumerate(listeners):
        handler_name = f"_handler_{idx}"
        listener_defs += (
            f"def {handler_name}(**kwargs):\n"
            f"    {body}\n"
        )
        listener_registrations += (
            f"        self.register_listener({ev_name!r}, {handler_name})\n"
        )

    cmd_defs = ""
    cmd_registrations = ""
    for idx, cmd_name in enumerate(command_names):
        func_name = f"_cmd_func_{idx}"
        cmd_defs += (
            f"def {func_name}(lib, opts, args):\n"
            f"    pass\n"
        )
        cmd_registrations += (
            f"        cmd_{idx} = ui.Subcommand({cmd_name!r}, "
            f"help='{plugin_name} command {idx}')\n"
            f"        cmd_{idx}.func = {func_name}\n"
            f"        cmds.append(cmd_{idx})\n"
        )

    class_name = "".join(
        p.capitalize() for p in plugin_name.replace("-", "_").split("_")
    )

    source = f'''# Auto-generated test plugin: {plugin_name}
from __future__ import annotations

from beets.plugins import BeetsPlugin
from beets import ui


{listener_defs}
{cmd_defs}


class {class_name}Plugin(BeetsPlugin):
    def __init__(self):
        super().__init__({plugin_name!r})
{listener_registrations}

    def commands(self):
        cmds = []
{cmd_registrations}
        return cmds
'''
    plugin_file.write_text(source)
    return plugin_file


@pytest.fixture
def real_plugin_factory(temp_plugin_dir):
    """Fixture for writing and loading real plugin packages.

    Returns a tuple of (beetsplug_dir, write_and_load) where
    beetsplug_dir is the directory to set as pluginpath, and
    write_and_load(name, ...) writes a plugin module and returns its name.
    """
    beetsplug_dir, _ns = temp_plugin_dir

    def _write_plugin(
        plugin_name: str,
        command_names: list[str] | None = None,
        listeners: list[tuple[str, str]] | None = None,
    ) -> str:
        write_real_plugin(beetsplug_dir, plugin_name, command_names, listeners)
        return plugin_name

    return beetsplug_dir, _write_plugin


class PluginLifecycleTestHelper(PytestPluginTestHelper):
    """Extended base class that exposes the shared factory helpers."""

    @staticmethod
    def make_command_plugin(*args: Any, **kwargs: Any):
        return make_command_plugin(*args, **kwargs)

    @staticmethod
    def make_listener_plugin(*args: Any, **kwargs: Any):
        return make_listener_plugin(*args, **kwargs)

    @staticmethod
    def make_template_field_plugin(*args: Any, **kwargs: Any):
        return make_template_field_plugin(*args, **kwargs)
