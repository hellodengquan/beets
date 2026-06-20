"""The 'doctor' command: diagnose plugin loading and dependency issues."""

from __future__ import annotations

import importlib
import shutil
import sys
from dataclasses import dataclass, field
from platform import python_version

import beets
from beets import plugins, ui
from beets.util.color import colorize


@dataclass
class DependencyInfo:
    python_packages: list[str] = field(default_factory=list)
    external_commands: list[str] = field(default_factory=list)


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


def _check_python_package(name: str) -> tuple[bool, str]:
    try:
        importlib.import_module(name)
        return True, ""
    except ImportError:
        return False, name


def _check_external_command(name: str) -> tuple[bool, str]:
    if shutil.which(name):
        return True, ""
    return False, name


def _get_configured_plugins() -> list[str]:
    return plugins.get_plugin_names()


def _get_loaded_plugin_names() -> set[str]:
    return {p.name for p in plugins.find_plugins()}


def _diagnose_plugin(
    name: str, loaded: set[str], details: bool = False
) -> list[str]:
    lines: list[str] = []
    is_loaded = name in loaded

    if is_loaded:
        status = colorize("text_success", "OK")
    else:
        status = colorize("text_error", "FAILED")

    lines.append(f"  {name}: {status}")

    dep_info = PLUGIN_DEPENDENCIES.get(name)
    if dep_info is None:
        if details:
            lines.append("    (no dependency info recorded)")
        return lines

    missing_packages: list[str] = []
    for pkg in dep_info.python_packages:
        available, _ = _check_python_package(pkg)
        if not available:
            missing_packages.append(pkg)

    missing_commands: list[str] = []
    for cmd in dep_info.external_commands:
        available, _ = _check_external_command(cmd)
        if not available:
            missing_commands.append(cmd)

    if details or not is_loaded:
        if missing_packages:
            pkg_status = colorize("text_error", "missing")
            lines.append(
                f"    Python packages ({pkg_status}): "
                f"{', '.join(missing_packages)}"
            )
        elif dep_info.python_packages and details:
            pkg_status = colorize("text_success", "all installed")
            lines.append(f"    Python packages ({pkg_status})")

        if missing_commands:
            cmd_status = colorize("text_error", "missing")
            lines.append(
                f"    External commands ({cmd_status}): "
                f"{', '.join(missing_commands)}"
            )
        elif dep_info.external_commands and details:
            cmd_status = colorize("text_success", "all found")
            lines.append(f"    External commands ({cmd_status})")

    return lines


def doctor_func(lib, opts, args):
    show_details = opts.details

    ui.print_(
        f"beets version {beets.__version__}  Python version {python_version()}"
    )
    ui.print_(f"Python path: {sys.executable}")
    ui.print_("")

    configured = _get_configured_plugins()
    loaded = _get_loaded_plugin_names()

    ui.print_(f"Plugins configured: {len(configured)}")
    ui.print_(f"Plugins loaded: {len(loaded)}")
    ui.print_("")

    if not configured:
        ui.print_("No plugins configured.")
        return

    ui.print_("Plugin Status:")
    failed_plugins: list[str] = []
    for name in sorted(configured):
        lines = _diagnose_plugin(name, loaded, details=show_details)
        for line in lines:
            ui.print_(line)
        if name not in loaded:
            failed_plugins.append(name)

    if failed_plugins:
        ui.print_("")
        ui.print_(
            colorize("text_error", "Failed to load: ")
            + ", ".join(sorted(failed_plugins))
        )
        ui.print_("")
        ui.print_("Suggestions:")
        for name in sorted(failed_plugins):
            dep_info = PLUGIN_DEPENDENCIES.get(name)
            if dep_info:
                missing_pkgs = [
                    pkg
                    for pkg in dep_info.python_packages
                    if not _check_python_package(pkg)[0]
                ]
                if missing_pkgs:
                    ui.print_(
                        f"  {name}: Install missing Python packages: "
                        f"{', '.join(missing_pkgs)}"
                    )
                missing_cmds = [
                    cmd
                    for cmd in dep_info.external_commands
                    if not _check_external_command(cmd)[0]
                ]
                if missing_cmds:
                    ui.print_(
                        f"  {name}: Install missing external commands: "
                        f"{', '.join(missing_cmds)}"
                    )
                if not missing_pkgs and not missing_cmds:
                    ui.print_(
                        f"  {name}: All known dependencies are present. "
                        f"Check logs with 'beet -vv' for details."
                    )
            else:
                ui.print_(
                    f"  {name}: No dependency info available. "
                    f"Check logs with 'beet -vv' for details."
                )


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
doctor_cmd.func = doctor_func

__all__ = ["doctor_cmd"]
