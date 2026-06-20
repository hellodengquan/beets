"""The 'doctor' command: diagnose plugin loading and dependency issues."""

from __future__ import annotations

import importlib
import json
import shutil
import sys
import traceback
from dataclasses import asdict, dataclass, field
from platform import python_version
from typing import Any

import beets
from beets import plugins, ui
from beets.exceptions import UserError
from beets.util.color import colorize


@dataclass
class DependencyInfo:
    python_packages: list[str] = field(default_factory=list)
    external_commands: list[str] = field(default_factory=list)


@dataclass
class PluginDiagnosis:
    name: str
    loaded: bool
    missing_python_packages: list[str] = field(default_factory=list)
    missing_external_commands: list[str] = field(default_factory=list)
    load_exception_type: str | None = None
    load_exception_message: str | None = None
    load_exception_traceback: str | None = None


PLUGIN_DEPENDENCIES: dict[str, DependencyInfo] = {
    "absubmit": DependencyInfo(
        python_packages=[], external_commands=["streaming_extractor_music"]
    ),
    "acousticbrainz": DependencyInfo(
        python_packages=[], external_commands=["streaming_extractor_music"]
    ),
    "autobpm": DependencyInfo(
        python_packages=["librosa", "resampy"], external_commands=[]
    ),
    "badfiles": DependencyInfo(
        python_packages=[], external_commands=["mp3val", "flac"]
    ),
    "bpd": DependencyInfo(python_packages=["gi"], external_commands=[]),
    "chroma": DependencyInfo(
        python_packages=["acoustid", "pyacoustid"],
        external_commands=["fpcalc", "chromaprint"],
    ),
    "convert": DependencyInfo(python_packages=[], external_commands=["ffmpeg"]),
    "discogs": DependencyInfo(
        python_packages=["discogs_client"], external_commands=[]
    ),
    "embedart": DependencyInfo(python_packages=["PIL"], external_commands=[]),
    "fetchart": DependencyInfo(
        python_packages=["bs4", "langdetect", "PIL", "requests"],
        external_commands=[],
    ),
    "import": DependencyInfo(
        python_packages=["py7zr", "rarfile"], external_commands=[]
    ),
    "keyfinder": DependencyInfo(
        python_packages=[], external_commands=["KeyFinder"]
    ),
    "lastgenre": DependencyInfo(
        python_packages=["pylast"], external_commands=[]
    ),
    "lastimport": DependencyInfo(
        python_packages=["pylast"], external_commands=[]
    ),
    "lyrics": DependencyInfo(
        python_packages=["bs4", "langdetect", "requests"], external_commands=[]
    ),
    "metasync": DependencyInfo(python_packages=["dbus"], external_commands=[]),
    "mpdstats": DependencyInfo(python_packages=["mpd"], external_commands=[]),
    "mbsubmit": DependencyInfo(
        python_packages=[], external_commands=["picard"]
    ),
    "plexupdate": DependencyInfo(
        python_packages=["requests"], external_commands=[]
    ),
    "replaygain": DependencyInfo(
        python_packages=["gi"],
        external_commands=["ffmpeg", "mp3gain", "aacgain"],
    ),
    "scrub": DependencyInfo(python_packages=["mutagen"], external_commands=[]),
    "sonosupdate": DependencyInfo(
        python_packages=["soco"], external_commands=[]
    ),
    "tidal": DependencyInfo(
        python_packages=["requests_oauthlib"], external_commands=[]
    ),
    "beatport": DependencyInfo(
        python_packages=["requests_oauthlib"], external_commands=[]
    ),
    "thumbnails": DependencyInfo(
        python_packages=["PIL", "pyxdg"], external_commands=[]
    ),
    "titlecase": DependencyInfo(
        python_packages=["titlecase"], external_commands=[]
    ),
    "web": DependencyInfo(
        python_packages=["flask", "flask_cors"], external_commands=[]
    ),
    "embyupdate": DependencyInfo(
        python_packages=["requests"], external_commands=[]
    ),
    "kodiupdate": DependencyInfo(
        python_packages=["requests"], external_commands=[]
    ),
    "aura": DependencyInfo(
        python_packages=["flask", "flask_cors", "PIL"], external_commands=[]
    ),
    "reflink": DependencyInfo(
        python_packages=["reflink"], external_commands=[]
    ),
}


def _check_python_package(name: str) -> tuple[bool, str | None]:
    try:
        importlib.import_module(name)
        return True, None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _check_external_command(name: str) -> tuple[bool, str | None]:
    if shutil.which(name):
        return True, None
    return False, "command not found in PATH"


def _get_configured_plugins() -> list[str]:
    return plugins.get_plugin_names()


def _get_loaded_plugin_names() -> set[str]:
    return {p.name for p in plugins.find_plugins()}


def _get_load_failure_map() -> dict[str, plugins.PluginLoadFailure]:
    return {f.name: f for f in plugins.plugin_load_failures()}


def _probe_import(name: str) -> tuple[str | None, str | None, str | None]:
    """Try to import the plugin module and return (exc_type, exc_msg, tb).

    This captures native-library ImportError, OSError, and any other
    exceptions beyond what a simple top-level ``importlib.import_module``
    would surface for the plugin's transitive dependencies (e.g. librosa
    failing because its native C extension is missing).
    """
    module_path = f"{plugins.PLUGIN_NAMESPACE}.{name}"
    try:
        if module_path in sys.modules:
            importlib.reload(sys.modules[module_path])
        else:
            importlib.import_module(module_path)
    except Exception as exc:
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        return (type(exc).__name__, str(exc), "".join(tb))
    return None, None, None


def _diagnose_plugin(name: str, loaded: set[str]) -> PluginDiagnosis:
    is_loaded = name in loaded
    dep_info = PLUGIN_DEPENDENCIES.get(name)

    missing_pkgs: list[str] = []
    missing_cmds: list[str] = []
    if dep_info:
        for pkg in dep_info.python_packages:
            available, _ = _check_python_package(pkg)
            if not available:
                missing_pkgs.append(pkg)
        for cmd in dep_info.external_commands:
            available, _ = _check_external_command(cmd)
            if not available:
                missing_cmds.append(cmd)

    exc_type = exc_msg = exc_tb = None
    if not is_loaded:
        failure = _get_load_failure_map().get(name)
        if failure:
            exc_type = failure.exception_type
            exc_msg = failure.exception_message
            cause = failure.exception.__cause__
            if cause is not None:
                exc_msg = (
                    f"{exc_msg} (caused by {type(cause).__name__}: {cause})"
                )
            exc_tb = "".join(
                traceback.format_exception(
                    type(failure.exception),
                    failure.exception,
                    failure.exception.__traceback__,
                )
            )
        else:
            exc_type, exc_msg, exc_tb = _probe_import(name)

    return PluginDiagnosis(
        name=name,
        loaded=is_loaded,
        missing_python_packages=missing_pkgs,
        missing_external_commands=missing_cmds,
        load_exception_type=exc_type,
        load_exception_message=exc_msg,
        load_exception_traceback=exc_tb,
    )


def _format_text(diagnoses: list[PluginDiagnosis], details: bool) -> None:
    ui.print_(
        f"beets version {beets.__version__}  Python version {python_version()}"
    )
    ui.print_(f"Python path: {sys.executable}")
    ui.print_("")

    configured_count = len(diagnoses)
    loaded_count = sum(1 for d in diagnoses if d.loaded)
    ui.print_(f"Plugins configured: {configured_count}")
    ui.print_(f"Plugins loaded: {loaded_count}")
    ui.print_("")

    if configured_count == 0:
        ui.print_("No plugins configured.")
        return

    ui.print_("Plugin Status:")
    failed: list[PluginDiagnosis] = []
    for diag in diagnoses:
        if diag.loaded:
            status = colorize("text_success", "OK")
        else:
            status = colorize("text_error", "FAILED")
            failed.append(diag)

        ui.print_(f"  {diag.name}: {status}")

        should_show = details or not diag.loaded

        if should_show:
            if diag.missing_python_packages:
                pkg_status = colorize("text_error", "missing")
                ui.print_(
                    f"    Python packages ({pkg_status}): "
                    f"{', '.join(diag.missing_python_packages)}"
                )
            elif (
                PLUGIN_DEPENDENCIES.get(diag.name)
                and PLUGIN_DEPENDENCIES[diag.name].python_packages
                and details
            ):
                pkg_status = colorize("text_success", "all installed")
                ui.print_(f"    Python packages ({pkg_status})")

            if diag.missing_external_commands:
                cmd_status = colorize("text_error", "missing")
                ui.print_(
                    f"    External commands ({cmd_status}): "
                    f"{', '.join(diag.missing_external_commands)}"
                )
            elif (
                PLUGIN_DEPENDENCIES.get(diag.name)
                and PLUGIN_DEPENDENCIES[diag.name].external_commands
                and details
            ):
                cmd_status = colorize("text_success", "all found")
                ui.print_(f"    External commands ({cmd_status})")

            if diag.load_exception_type and not diag.loaded:
                ui.print_(
                    f"    Load error ({diag.load_exception_type}): "
                    f"{diag.load_exception_message}"
                )

            if (
                diag.name not in PLUGIN_DEPENDENCIES
                and diag.name not in {d.name for d in failed}
                and details
            ):
                ui.print_("    (no dependency info recorded)")

    if failed:
        ui.print_("")
        ui.print_(
            colorize("text_error", "Failed to load: ")
            + ", ".join(sorted(d.name for d in failed))
        )
        ui.print_("")
        ui.print_("Suggestions:")
        for diag in sorted(failed, key=lambda d: d.name):
            dep_info = PLUGIN_DEPENDENCIES.get(diag.name)
            if dep_info:
                if diag.missing_python_packages:
                    ui.print_(
                        f"  {diag.name}: Install missing Python packages: "
                        f"{', '.join(diag.missing_python_packages)}"
                    )
                if diag.missing_external_commands:
                    ui.print_(
                        f"  {diag.name}: Install missing external commands: "
                        f"{', '.join(diag.missing_external_commands)}"
                    )
                if (
                    not diag.missing_python_packages
                    and not diag.missing_external_commands
                ):
                    ui.print_(
                        f"  {diag.name}: All known dependencies are present. "
                        f"Use 'beet -vv' to diagnose further."
                    )
            else:
                ui.print_(
                    f"  {diag.name}: No dependency info available. "
                    f"Use 'beet -vv' to diagnose further."
                )


def _format_json(diagnoses: list[PluginDiagnosis]) -> None:
    report: dict[str, Any] = {
        "beets_version": beets.__version__,
        "python_version": python_version(),
        "python_executable": sys.executable,
        "plugins_configured": len(diagnoses),
        "plugins_loaded": sum(1 for d in diagnoses if d.loaded),
        "plugins": [asdict(d) for d in diagnoses],
    }
    ui.print_(json.dumps(report, indent=2, ensure_ascii=False, default=str))


def doctor_func(lib, opts, args):
    output_format: str = (getattr(opts, "format", "text") or "text").lower()
    if output_format not in ("text", "json"):
        raise UserError(
            f"unsupported output format {output_format!r}; use 'text' or 'json'"
        )
    show_details: bool = opts.details

    configured = _get_configured_plugins()
    loaded = _get_loaded_plugin_names()
    diagnoses = [_diagnose_plugin(name, loaded) for name in sorted(configured)]

    if output_format == "json":
        _format_json(diagnoses)
    else:
        _format_text(diagnoses, details=show_details)


doctor_cmd = ui.Subcommand(
    "doctor", help="diagnose plugin and dependency issues"
)
doctor_cmd.parser.add_option(
    "-d",
    "--details",
    action="store_true",
    default=False,
    help="show detailed dependency info for all plugins",
)
doctor_cmd.parser.add_option(
    "-f",
    "--format",
    dest="format",
    default="text",
    help="output format: text (default) or json (for CI)",
)
doctor_cmd.func = doctor_func

__all__ = ["doctor_cmd"]
