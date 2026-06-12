"""The 'check' command: perform plugin health check."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass, field
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


CATEGORIES_ORDER = [
    CheckCategory.ENABLED,
    CheckCategory.DEPENDENCY,
    CheckCategory.COMMAND,
    CheckCategory.CONFIG,
]

STATUS_STYLE: dict[CheckStatus, str] = {
    CheckStatus.OK: "text_success",
    CheckStatus.WARNING: "text_warning",
    CheckStatus.ERROR: "text_error",
}

SCHEMA_VERSION: str = "1.0.0"

TOP_LEVEL_FIELDS: frozenset[str] = frozenset(
    {
        "schema_version",
        "ok_count",
        "warning_count",
        "error_count",
        "has_errors",
        "checks",
    }
)

CHECK_ITEM_FIELDS: frozenset[str] = frozenset(
    {"plugin", "category", "status", "message", "suggestion"}
)

VALID_CATEGORIES: frozenset[str] = frozenset(c.value for c in CheckCategory)

VALID_STATUSES: frozenset[str] = frozenset(s.value for s in CheckStatus)


@dataclass
class CheckResult:
    plugin: str
    category: CheckCategory
    status: CheckStatus
    message: str
    suggestion: str = ""

    def to_dict(self) -> dict[str, str]:
        category_val = self.category.value
        status_val = self.status.value
        if category_val not in VALID_CATEGORIES:
            raise ValueError(
                f"Invalid category value: {category_val!r} "
                f"(allowed: {sorted(VALID_CATEGORIES)})"
            )
        if status_val not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status value: {status_val!r} "
                f"(allowed: {sorted(VALID_STATUSES)})"
            )
        return {
            "plugin": self.plugin,
            "category": category_val,
            "status": status_val,
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass
class HealthReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.status == CheckStatus.OK)

    @property
    def warning_count(self) -> int:
        return sum(1 for r in self.results if r.status == CheckStatus.WARNING)

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.results if r.status == CheckStatus.ERROR)

    @property
    def has_errors(self) -> bool:
        return self.error_count > 0

    def to_dict(self) -> dict[str, Any]:
        by_category: dict[str, list[dict[str, str]]] = {}
        for cat in CATEGORIES_ORDER:
            items = [
                r.to_dict()
                for r in self.results
                if r.category == cat
            ]
            if items:
                cat_key = cat.value
                if cat_key not in VALID_CATEGORIES:
                    raise ValueError(
                        f"Invalid category key: {cat_key!r} "
                        f"(allowed: {sorted(VALID_CATEGORIES)})"
                    )
                by_category[cat_key] = items

        for key in by_category:
            if key not in VALID_CATEGORIES:
                raise ValueError(
                    f"Invalid checks key: {key!r} "
                    f"(allowed: {sorted(VALID_CATEGORIES)})"
                )

        return {
            "schema_version": SCHEMA_VERSION,
            "ok_count": self.ok_count,
            "warning_count": self.warning_count,
            "error_count": self.error_count,
            "has_errors": self.has_errors,
            "checks": by_category,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


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
                            message=(
                                f"Plugin '{name}' has a "
                                f"configuration error: {exc}"
                            ),
                            suggestion=(
                                f"Check the '{name}' section in your "
                                f"config file. Run 'beet config -d' to "
                                f"see the full configuration"
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


def _collect_results(
    configured: list[str],
    disabled: set[str],
    loaded: set[str],
    filter_plugins: set[str] | None = None,
) -> HealthReport:
    all_results: list[CheckResult] = []
    all_results.extend(_check_enabled(configured, disabled, loaded))
    all_results.extend(_check_dependencies(configured, loaded))
    all_results.extend(_check_commands(configured, loaded))
    all_results.extend(_check_config(loaded))

    if filter_plugins:
        all_results = [
            r for r in all_results if r.plugin in filter_plugins
        ]

    return HealthReport(results=all_results)


def _print_text_report(report: HealthReport) -> None:
    ui.print_("")
    ui.print_(colorize("text_highlight", "Plugin Health Check"))
    ui.print_(colorize("text_faint", "=" * 50))

    for category in CATEGORIES_ORDER:
        cat_results = [
            r for r in report.results if r.category == category
        ]
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

    ui.print_("")
    ui.print_(colorize("text_faint", "-" * 50))
    summary_parts = [f"{report.ok_count} ok"]
    if report.warning_count:
        summary_parts.append(
            colorize(
                "text_warning", f"{report.warning_count} warning(s)"
            )
        )
    if report.error_count:
        summary_parts.append(
            colorize("text_error", f"{report.error_count} error(s)")
        )
    ui.print_(f"Summary: {', '.join(summary_parts)}")
    ui.print_("")


def _print_json_report(report: HealthReport) -> None:
    ui.print_(report.to_json())


def run_health_check(lib: Library, opts: Any, args: list[str]) -> None:
    configured = _get_configured_plugins()
    disabled = _get_disabled_plugins()
    loaded = _get_loaded_plugin_names()

    filter_plugins: set[str] | None = None
    if args:
        filter_plugins = set(args)

    report = _collect_results(configured, disabled, loaded, filter_plugins)

    fmt = getattr(opts, "format", "text")
    if fmt == "json":
        _print_json_report(report)
    else:
        _print_text_report(report)


check_cmd = ui.Subcommand(
    "check",
    help="check plugin health status",
    aliases=("doctor",),
)
check_cmd.parser.add_option(
    "-f",
    "--format",
    dest="format",
    default="text",
    help="output format: text (default) or json",
)
check_cmd.func = run_health_check

__all__ = [
    "CHECK_ITEM_FIELDS",
    "SCHEMA_VERSION",
    "TOP_LEVEL_FIELDS",
    "VALID_CATEGORIES",
    "VALID_STATUSES",
    "CheckCategory",
    "CheckResult",
    "CheckStatus",
    "HealthReport",
    "check_cmd",
]
