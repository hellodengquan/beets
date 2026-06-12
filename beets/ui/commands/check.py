"""The 'check' command: perform plugin health check."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

import confuse

from beets import config, logging, plugins, ui
from beets.util.color import colorize

if TYPE_CHECKING:
    from beets.library import Library

log = logging.getLogger("beets")


PLUGIN_DEPS: dict[str, list[str]] = {
    "absubmit": ["requests"],
    "aura": ["flask", "flask_cors", "PIL"],
    "autobpm": ["librosa", "resampy"],
    "beatport": ["requests_oauthlib"],
    "bpd": ["gi"],
    "chroma": ["acoustid"],
    "discogs": ["discogs_client"],
    "embedart": ["PIL"],
    "embyupdate": ["requests"],
    "fetchart": ["beautifulsoup4", "langdetect", "PIL", "requests"],
    "import": ["py7zr", "rarfile"],
    "kodiupdate": ["requests"],
    "lastgenre": ["pylast"],
    "lastimport": ["pylast"],
    "lyrics": ["beautifulsoup4", "langdetect", "requests"],
    "metasync": ["dbus"],
    "mpdstats": ["mpd"],
    "plexupdate": ["requests"],
    "reflink": ["reflink"],
    "replaygain": ["gi"],
    "scrub": ["mutagen"],
    "sonosupdate": ["soco"],
    "tidal": ["requests_oauthlib"],
    "titlecase": ["titlecase"],
    "thumbnails": ["PIL", "xdg"],
    "web": ["flask", "flask_cors"],
}


class CheckStatus(Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


class CheckCategory(Enum):
    ENABLED = "enabled"
    DEPENDENCY = "dependency"
    COMMAND = "command"
    CONFIG = "config"


STATUS_STYLE: dict[CheckStatus, str] = {
    CheckStatus.OK: "text_success",
    CheckStatus.WARNING: "text_warning",
    CheckStatus.ERROR: "text_error",
}


@dataclass
class CheckResult:
    plugin: str
    category: CheckCategory
    status: CheckStatus
    message: str
    suggestion: str = ""


def _status_label(status: CheckStatus) -> str:
    style = STATUS_STYLE[status]
    labels = {
        CheckStatus.OK: "OK",
        CheckStatus.WARNING: "WARN",
        CheckStatus.ERROR: "FAIL",
    }
    return colorize(style, labels[status])


def _is_package_available(package_name: str) -> bool:
    return importlib.util.find_spec(package_name) is not None


def _get_configured_plugins() -> list[str]:
    try:
        return list(config["plugins"].as_str_seq())
    except confuse.ConfigError:
        return []


def _get_disabled_plugins() -> set[str]:
    try:
        config.add({"disabled_plugins": []})
        return set(config["disabled_plugins"].as_str_seq())
    except confuse.ConfigError:
        return set()


def _get_loaded_plugin_names() -> set[str]:
    return {p.name for p in plugins.find_plugins()}


def _check_enabled(
    configured: list[str],
    disabled: set[str],
    loaded: set[str],
) -> list[CheckResult]:
    results: list[CheckResult] = []

    for name in configured:
        if name in disabled:
            results.append(
                CheckResult(
                    plugin=name,
                    category=CheckCategory.ENABLED,
                    status=CheckStatus.WARNING,
                    message=(
                        f"Plugin '{name}' is configured but disabled "
                        f"via disabled_plugins"
                    ),
                    suggestion=(
                        f"Remove '{name}' from the plugins list, "
                        f"or remove it from disabled_plugins"
                    ),
                )
            )
        elif name not in loaded:
            results.append(
                CheckResult(
                    plugin=name,
                    category=CheckCategory.ENABLED,
                    status=CheckStatus.ERROR,
                    message=f"Plugin '{name}' is configured but failed to load",
                    suggestion=(
                        f"Check that the module 'beetsplug.{name}' "
                        f"exists and has no import errors. "
                        f"Run 'beet -vv check' for more details"
                    ),
                )
            )
        else:
            results.append(
                CheckResult(
                    plugin=name,
                    category=CheckCategory.ENABLED,
                    status=CheckStatus.OK,
                    message=f"Plugin '{name}' is loaded",
                )
            )

    return results


def _check_dependencies(configured: list[str], loaded: set[str]) -> list[CheckResult]:
    results: list[CheckResult] = []

    for name in configured:
        if name not in loaded:
            continue

        deps = PLUGIN_DEPS.get(name, [])
        if not deps:
            results.append(
                CheckResult(
                    plugin=name,
                    category=CheckCategory.DEPENDENCY,
                    status=CheckStatus.OK,
                    message=f"Plugin '{name}' has no extra dependencies",
                )
            )
            continue

        missing = [d for d in deps if not _is_package_available(d)]
        if missing:
            results.append(
                CheckResult(
                    plugin=name,
                    category=CheckCategory.DEPENDENCY,
                    status=CheckStatus.ERROR,
                    message=(
                        f"Plugin '{name}' is missing dependencies: "
                        + ", ".join(missing)
                    ),
                    suggestion=(
                        f"Install missing dependencies with: "
                        f"pip install beets[{name}]"
                    ),
                )
            )
        else:
            results.append(
                CheckResult(
                    plugin=name,
                    category=CheckCategory.DEPENDENCY,
                    status=CheckStatus.OK,
                    message=f"Plugin '{name}' dependencies are satisfied",
                )
            )

    return results


def _check_commands(configured: list[str], loaded: set[str]) -> list[CheckResult]:
    results: list[CheckResult] = []
    loaded_plugins = {p.name: p for p in plugins.find_plugins()}

    for name in configured:
        if name not in loaded:
            continue

        plugin = loaded_plugins.get(name)
        if plugin is None:
            continue

        plugin_cmds = plugin.commands()
        if not plugin_cmds:
            results.append(
                CheckResult(
                    plugin=name,
                    category=CheckCategory.COMMAND,
                    status=CheckStatus.OK,
                    message=f"Plugin '{name}' does not register commands",
                )
            )
            continue

        cmd_names = [c.name for c in plugin_cmds]
        for cmd in plugin_cmds:
            for alias in getattr(cmd, "aliases", ()):
                cmd_names.append(f"{cmd.name} (alias: {alias})")

        results.append(
            CheckResult(
                plugin=name,
                category=CheckCategory.COMMAND,
                status=CheckStatus.OK,
                message=(
                    f"Plugin '{name}' registers commands: "
                    + ", ".join(cmd_names)
                ),
            )
        )

    cmd_to_plugin: dict[str, str] = {}
    for p in plugins.find_plugins():
        for cmd in p.commands():
            for alias in [cmd.name, *list(getattr(cmd, "aliases", ()))]:
                if alias in cmd_to_plugin:
                    results.append(
                        CheckResult(
                            plugin=p.name,
                            category=CheckCategory.COMMAND,
                            status=CheckStatus.WARNING,
                            message=(
                                f"Command '{alias}' is registered by both "
                                f"'{cmd_to_plugin[alias]}' and '{p.name}'"
                            ),
                            suggestion=(
                                "Disable one of the conflicting plugins, or "
                                "use the full subcommand name to disambiguate"
                            ),
                        )
                    )
                else:
                    cmd_to_plugin[alias] = p.name

    return results


def _check_config(loaded: set[str]) -> list[CheckResult]:
    results: list[CheckResult] = []
    loaded_plugins = list(plugins.find_plugins())

    item_fields: dict[str, str] = {}
    album_fields: dict[str, str] = {}
    for plugin in loaded_plugins:
        for field_name, func in plugin.template_fields.items():
            if field_name in item_fields:
                results.append(
                    CheckResult(
                        plugin=plugin.name,
                        category=CheckCategory.CONFIG,
                        status=CheckStatus.WARNING,
                        message=(
                            f"Template field '{field_name}' is defined by both "
                            f"'{item_fields[field_name]}' and '{plugin.name}'"
                        ),
                        suggestion=(
                            "Disable one of the conflicting plugins to "
                            "resolve the field name collision"
                        ),
                    )
                )
            else:
                item_fields[field_name] = plugin.name

        for field_name, func in plugin.album_template_fields.items():
            if field_name in album_fields:
                results.append(
                    CheckResult(
                        plugin=plugin.name,
                        category=CheckCategory.CONFIG,
                        status=CheckStatus.WARNING,
                        message=(
                            f"Album template field '{field_name}' is defined by "
                            f"both '{album_fields[field_name]}' and "
                            f"'{plugin.name}'"
                        ),
                        suggestion=(
                            "Disable one of the conflicting plugins to "
                            "resolve the field name collision"
                        ),
                    )
                )
            else:
                album_fields[field_name] = plugin.name

    for name in loaded:
        for plugin in loaded_plugins:
            if plugin.name == name:
                try:
                    plugin.config.get({})
                except confuse.ConfigError as exc:
                    results.append(
                        CheckResult(
                            plugin=name,
                            category=CheckCategory.CONFIG,
                            status=CheckStatus.ERROR,
                            message=f"Plugin '{name}' has a configuration error: {exc}",
                            suggestion=(
                                f"Check the '{name}' section in your config file. "
                                f"Run 'beet config -d' to see the full configuration"
                            ),
                        )
                    )
                break

    if not any(
        r.category == CheckCategory.CONFIG and r.status != CheckStatus.OK
        for r in results
    ):
        for name in sorted(loaded):
            results.append(
                CheckResult(
                    plugin=name,
                    category=CheckCategory.CONFIG,
                    status=CheckStatus.OK,
                    message=f"Plugin '{name}' configuration is valid",
                )
            )

    return results


def run_health_check(lib: Library, opts: Any, args: list[str]) -> None:
    configured = _get_configured_plugins()
    disabled = _get_disabled_plugins()
    loaded = _get_loaded_plugin_names()

    ui.print_("")
    ui.print_(colorize("text_highlight", "Plugin Health Check"))
    ui.print_(colorize("text_faint", "=" * 50))

    filter_plugins: set[str] = set()
    if args:
        filter_plugins = set(args)

    all_results: list[CheckResult] = []

    all_results.extend(_check_enabled(configured, disabled, loaded))
    all_results.extend(_check_dependencies(configured, loaded))
    all_results.extend(_check_commands(configured, loaded))
    all_results.extend(_check_config(loaded))

    if filter_plugins:
        all_results = [r for r in all_results if r.plugin in filter_plugins]

    categories_order = [
        CheckCategory.ENABLED,
        CheckCategory.DEPENDENCY,
        CheckCategory.COMMAND,
        CheckCategory.CONFIG,
    ]

    for category in categories_order:
        cat_results = [r for r in all_results if r.category == category]
        if not cat_results:
            continue

        ui.print_("")
        ui.print_(colorize("action", f"  {category.value.upper()}"))

        for result in cat_results:
            label = _status_label(result.status)
            line = f"    [{label}] {result.message}"
            ui.print_(line)
            if result.suggestion:
                ui.print_(
                    colorize(
                        "text_faint",
                        f"Suggestion: {result.suggestion}",
                    )
                )

    error_count = sum(1 for r in all_results if r.status == CheckStatus.ERROR)
    warn_count = sum(1 for r in all_results if r.status == CheckStatus.WARNING)
    ok_count = sum(1 for r in all_results if r.status == CheckStatus.OK)

    ui.print_("")
    ui.print_(colorize("text_faint", "-" * 50))
    summary_parts = [f"{ok_count} ok"]
    if warn_count:
        summary_parts.append(
            colorize("text_warning", f"{warn_count} warning(s)")
        )
    if error_count:
        summary_parts.append(
            colorize("text_error", f"{error_count} error(s)")
        )
    ui.print_(f"Summary: {', '.join(summary_parts)}")
    ui.print_("")


check_cmd = ui.Subcommand(
    "check",
    help="check plugin health status",
    aliases=("doctor",),
)
check_cmd.func = run_health_check

__all__ = ["check_cmd"]
