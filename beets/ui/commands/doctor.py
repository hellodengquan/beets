"""The 'doctor' command: diagnose plugin loading and dependency issues."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import traceback
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
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
    import_timed_out: bool = False
    has_dependency_info: bool = False
    known_python_packages: list[str] = field(default_factory=list)
    known_external_commands: list[str] = field(default_factory=list)


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
    "beatport": DependencyInfo(
        python_packages=["requests_oauthlib"], external_commands=[]
    ),
    "bpd": DependencyInfo(python_packages=["gi"], external_commands=[]),
    "chroma": DependencyInfo(
        python_packages=["acoustid", "chromaprint"],
        external_commands=["fpcalc", "chromaprint"],
    ),
    "convert": DependencyInfo(python_packages=[], external_commands=["ffmpeg"]),
    "discogs": DependencyInfo(
        python_packages=["discogs_client"], external_commands=[]
    ),
    "duplicates": DependencyInfo(python_packages=[], external_commands=[]),
    "edit": DependencyInfo(python_packages=[], external_commands=[]),
    "embedart": DependencyInfo(python_packages=["PIL"], external_commands=[]),
    "embyupdate": DependencyInfo(
        python_packages=["requests"], external_commands=[]
    ),
    "export": DependencyInfo(python_packages=[], external_commands=[]),
    "fetchart": DependencyInfo(
        python_packages=["bs4", "langdetect", "PIL", "requests"],
        external_commands=[],
    ),
    "filefilter": DependencyInfo(python_packages=[], external_commands=[]),
    "freedesktop": DependencyInfo(
        python_packages=["pyxdg", "dbus"], external_commands=[]
    ),
    "fromfilename": DependencyInfo(python_packages=[], external_commands=[]),
    "ftintitle": DependencyInfo(python_packages=[], external_commands=[]),
    "hook": DependencyInfo(python_packages=[], external_commands=[]),
    "import": DependencyInfo(
        python_packages=["py7zr", "rarfile"], external_commands=[]
    ),
    "importfeeds": DependencyInfo(python_packages=[], external_commands=[]),
    "info": DependencyInfo(python_packages=[], external_commands=[]),
    "inline": DependencyInfo(python_packages=[], external_commands=[]),
    "keyfinder": DependencyInfo(
        python_packages=[], external_commands=["KeyFinder"]
    ),
    "kodiupdate": DependencyInfo(
        python_packages=["requests"], external_commands=[]
    ),
    "lastgenre": DependencyInfo(
        python_packages=["pylast"], external_commands=[]
    ),
    "lastimport": DependencyInfo(
        python_packages=["pylast"], external_commands=[]
    ),
    "limit": DependencyInfo(python_packages=[], external_commands=[]),
    "loadext": DependencyInfo(python_packages=[], external_commands=[]),
    "lyrics": DependencyInfo(
        python_packages=["bs4", "langdetect", "requests"], external_commands=[]
    ),
    "mbcollection": DependencyInfo(python_packages=[], external_commands=[]),
    "mbsubmit": DependencyInfo(
        python_packages=[], external_commands=["picard"]
    ),
    "mbsync": DependencyInfo(python_packages=[], external_commands=[]),
    "metasync": DependencyInfo(python_packages=["dbus"], external_commands=[]),
    "missing": DependencyInfo(python_packages=[], external_commands=[]),
    "mpdstats": DependencyInfo(python_packages=["mpd"], external_commands=[]),
    "mpdupdate": DependencyInfo(python_packages=["mpd"], external_commands=[]),
    "musicbrainz": DependencyInfo(python_packages=[], external_commands=[]),
    "parentwork": DependencyInfo(python_packages=[], external_commands=[]),
    "permissions": DependencyInfo(python_packages=[], external_commands=[]),
    "play": DependencyInfo(python_packages=[], external_commands=[]),
    "plexupdate": DependencyInfo(
        python_packages=["requests"], external_commands=[]
    ),
    "playlist": DependencyInfo(python_packages=[], external_commands=[]),
    "random": DependencyInfo(python_packages=[], external_commands=[]),
    "reflink": DependencyInfo(
        python_packages=["reflink"], external_commands=[]
    ),
    "replaygain": DependencyInfo(
        python_packages=["gi"],
        external_commands=["ffmpeg", "mp3gain", "aacgain"],
    ),
    "scrub": DependencyInfo(python_packages=["mutagen"], external_commands=[]),
    "smartplaylist": DependencyInfo(python_packages=[], external_commands=[]),
    "sonosupdate": DependencyInfo(
        python_packages=["soco"], external_commands=[]
    ),
    "spotify": DependencyInfo(
        python_packages=["requests", "requests_oauthlib"], external_commands=[]
    ),
    "tidal": DependencyInfo(
        python_packages=["requests_oauthlib"], external_commands=[]
    ),
    "thumbnails": DependencyInfo(
        python_packages=["PIL", "pyxdg"], external_commands=[]
    ),
    "titlecase": DependencyInfo(
        python_packages=["titlecase"], external_commands=[]
    ),
    "type": DependencyInfo(python_packages=[], external_commands=[]),
    "unimported": DependencyInfo(python_packages=[], external_commands=[]),
    "update": DependencyInfo(python_packages=[], external_commands=[]),
    "web": DependencyInfo(
        python_packages=["flask", "flask_cors"], external_commands=[]
    ),
    "aura": DependencyInfo(
        python_packages=["flask", "flask_cors", "PIL"], external_commands=[]
    ),
}


HEAVY_PACKAGES: frozenset[str] = frozenset(
    {
        "librosa",
        "resampy",
        "gi",
        "chromaprint",
        "acoustid",
        "mutagen",
        "soco",
        "flask",
        "flask_cors",
        "dbus",
    }
)

DEFAULT_IMPORT_TIMEOUT_SECONDS = 2.0

FATAL_EXCEPTION_TYPES: tuple[type[BaseException], ...] = (
    RecursionError,
    MemoryError,
    KeyboardInterrupt,
    SystemExit,
)


TRANSLATIONS: dict[str, dict[str, str]] = {
    "zh": {
        "header_environment": "运行环境",
        "label_python_path": "Python 路径",
        "label_plugins_configured": "已配置插件数",
        "label_plugins_loaded": "已成功加载插件数",
        "msg_no_plugins": "没有配置任何插件。",
        "section_plugin_status": "插件状态",
        "status_ok": "正常",
        "status_failed": "加载失败",
        "status_timeout": "超时",
        "label_python_packages": "Python 依赖包",
        "label_external_commands": "外部命令",
        "state_missing": "缺失",
        "state_installed": "全部已安装",
        "state_found": "全部已找到",
        "label_load_error": "加载错误",
        "msg_no_dep_info": "(未登记该插件的依赖信息)",
        "msg_import_timeout": (
            "导入诊断超时 (可能是重量级依赖初始化缓慢, 使用 --timeout 调整)"
        ),
        "section_failed_to_load": "以下插件加载失败",
        "section_suggestions": "修复建议",
        "hint_install_python_packages": "安装缺失的 Python 包",
        "hint_install_external_commands": "安装缺失的外部命令",
        "hint_all_deps_present": (
            "已知依赖全部满足, 使用 'beet -vv' 获取详细日志进一步排查"
        ),
        "hint_no_dep_info_available": (
            "未登记依赖信息, 使用 'beet -vv' 获取详细日志进一步排查"
        ),
        "help_doctor": "诊断插件加载和依赖问题",
        "help_details": "显示所有插件的详细依赖信息",
        "help_format": "输出格式: text (默认) 或 json (用于 CI)",
        "help_timeout": "单个插件导入诊断超时(秒), 默认 2",
        "error_unsupported_format": (
            "不支持的输出格式 {fmt!r}, 请使用 'text' 或 'json'"
        ),
        "label_fatal_exception": "致命异常",
        "msg_fatal_exception": "插件模块触发致命异常 {etype}",
        "section_header_environment": (
            "beets 版本 {beets_ver}  |  Python 版本 {py_ver}"
        ),
    },
    "en": {
        "header_environment": "Environment",
        "label_python_path": "Python path",
        "label_plugins_configured": "Plugins configured",
        "label_plugins_loaded": "Plugins loaded",
        "msg_no_plugins": "No plugins configured.",
        "section_plugin_status": "Plugin Status",
        "status_ok": "OK",
        "status_failed": "FAILED",
        "status_timeout": "TIMEOUT",
        "label_python_packages": "Python packages",
        "label_external_commands": "External commands",
        "state_missing": "missing",
        "state_installed": "all installed",
        "state_found": "all found",
        "label_load_error": "Load error",
        "msg_no_dep_info": "(no dependency info recorded)",
        "msg_import_timeout": (
            "import probe timed out (heavy dependency initialisation, "
            "adjust with --timeout)"
        ),
        "section_failed_to_load": "Failed to load",
        "section_suggestions": "Suggestions",
        "hint_install_python_packages": "Install missing Python packages",
        "hint_install_external_commands": "Install missing external commands",
        "hint_all_deps_present": (
            "All known dependencies are present. Use 'beet -vv' for "
            "further diagnosis."
        ),
        "hint_no_dep_info_available": (
            "No dependency info available. Use 'beet -vv' for "
            "further diagnosis."
        ),
        "help_doctor": "diagnose plugin and dependency issues",
        "help_details": "show detailed dependency info for all plugins",
        "help_format": "output format: text (default) or json (for CI)",
        "help_timeout": "import probe timeout per plugin in seconds (default 2)",
        "error_unsupported_format": (
            "unsupported output format {fmt!r}; use 'text' or 'json'"
        ),
        "label_fatal_exception": "Fatal exception",
        "msg_fatal_exception": ("plugin module raised fatal exception {etype}"),
        "section_header_environment": (
            "beets version {beets_ver}  Python version {py_ver}"
        ),
    },
}


def _detect_language() -> str:
    try:
        cfg_lang = beets.config["ui"]["language"].as_str()
    except Exception:
        cfg_lang = None
    if cfg_lang:
        return cfg_lang.lower().split("_")[0]
    env_lang = os.environ.get("LANG", "") or os.environ.get("LC_ALL", "")
    if env_lang:
        return env_lang.split(".")[0].split("_")[0].lower()
    return "en"


def _translate(key: str, lang: str | None = None, **kwargs: Any) -> str:
    if lang is None:
        lang = _detect_language()
    table = TRANSLATIONS.get(lang, {}) or TRANSLATIONS.get(lang[:2], {})
    text = table.get(key, key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            pass
    return text


def _is_heavy_python_package(name: str) -> bool:
    return name in HEAVY_PACKAGES


def _import_in_worker(module_path: str) -> None:
    if module_path in sys.modules:
        importlib.reload(sys.modules[module_path])
    else:
        importlib.import_module(module_path)


def _probe_import(
    name: str, timeout: float = DEFAULT_IMPORT_TIMEOUT_SECONDS
) -> tuple[str | None, str | None, str | None, bool]:
    """Try to import the plugin module with a timeout.

    Returns ``(exc_type, exc_msg, exc_tb, timed_out)``.

    - Uses a fresh background thread per call to shield the main CLI loop
      from expensive module initialisation (librosa, gi, mutagen, etc.)
      and to avoid state contamination across calls.
    - Fatal exceptions (RecursionError, MemoryError, KeyboardInterrupt,
      SystemExit) are caught on both sides of the thread boundary and
      converted to friendly error messages without a traceback.
    """
    module_path = f"{plugins.PLUGIN_NAMESPACE}.{name}"

    def _run() -> tuple[str | None, str | None, str | None, bool]:
        try:
            _import_in_worker(module_path)
        except FATAL_EXCEPTION_TYPES as exc:
            return (
                type(exc).__name__,
                _translate("msg_fatal_exception", etype=type(exc).__name__)
                + f": {exc}",
                None,
                False,
            )
        except Exception as exc:
            tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
            return (type(exc).__name__, str(exc), "".join(tb), False)
        return None, None, None, False

    with ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="doctor_probe"
    ) as executor:
        future: Future = executor.submit(_run)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()
            return (
                "TimeoutError",
                _translate("msg_import_timeout"),
                None,
                True,
            )


def _check_python_package(
    name: str, timeout: float = DEFAULT_IMPORT_TIMEOUT_SECONDS
) -> tuple[bool, str | None]:
    try:
        if not _is_heavy_python_package(name):
            _import_in_worker(name)
            return True, None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

    if not _is_heavy_python_package(name):
        return True, None

    def _try_import() -> tuple[bool, str | None]:
        try:
            importlib.import_module(name)
            return True, None
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="doctor_depcheck"
    ) as executor:
        future: Future = executor.submit(_try_import)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()
            return False, "timeout"


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


def _diagnose_plugin(
    name: str, loaded: set[str], timeout: float = DEFAULT_IMPORT_TIMEOUT_SECONDS
) -> PluginDiagnosis:
    is_loaded = name in loaded
    dep_info = PLUGIN_DEPENDENCIES.get(name)

    missing_pkgs: list[str] = []
    missing_cmds: list[str] = []
    known_pkgs: list[str] = []
    known_cmds: list[str] = []
    has_info = dep_info is not None

    if dep_info:
        known_pkgs = list(dep_info.python_packages)
        known_cmds = list(dep_info.external_commands)
        for pkg in dep_info.python_packages:
            available, _ = _check_python_package(pkg, timeout=timeout)
            if not available:
                missing_pkgs.append(pkg)
        for cmd in dep_info.external_commands:
            available, _ = _check_external_command(cmd)
            if not available:
                missing_cmds.append(cmd)

    exc_type = exc_msg = exc_tb = None
    timed_out = False
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
            exc_type, exc_msg, exc_tb, timed_out = _probe_import(
                name, timeout=timeout
            )

    return PluginDiagnosis(
        name=name,
        loaded=is_loaded,
        missing_python_packages=missing_pkgs,
        missing_external_commands=missing_cmds,
        load_exception_type=exc_type,
        load_exception_message=exc_msg,
        load_exception_traceback=exc_tb,
        import_timed_out=timed_out,
        has_dependency_info=has_info,
        known_python_packages=known_pkgs,
        known_external_commands=known_cmds,
    )


def _render_plugin_text(
    diag: PluginDiagnosis, details: bool, lang: str
) -> list[str]:
    if diag.loaded:
        status = colorize("text_success", _translate("status_ok", lang=lang))
    elif diag.import_timed_out:
        status = colorize(
            "text_warning", _translate("status_timeout", lang=lang)
        )
    else:
        status = colorize("text_error", _translate("status_failed", lang=lang))

    lines = [f"  {diag.name}: {status}"]
    should_show = details or not diag.loaded

    if not should_show:
        return lines

    label_py = _translate("label_python_packages", lang=lang)
    label_cmd = _translate("label_external_commands", lang=lang)
    st_missing = colorize("text_error", _translate("state_missing", lang=lang))
    st_installed = colorize(
        "text_success", _translate("state_installed", lang=lang)
    )
    st_found = colorize("text_success", _translate("state_found", lang=lang))

    if diag.missing_python_packages:
        lines.append(
            f"    {label_py} ({st_missing}): "
            f"{', '.join(diag.missing_python_packages)}"
        )
    elif diag.known_python_packages and details:
        lines.append(f"    {label_py} ({st_installed})")

    if diag.missing_external_commands:
        lines.append(
            f"    {label_cmd} ({st_missing}): "
            f"{', '.join(diag.missing_external_commands)}"
        )
    elif diag.known_external_commands and details:
        lines.append(f"    {label_cmd} ({st_found})")

    if diag.import_timed_out and not diag.loaded:
        lines.append(
            f"    {_translate('label_load_error', lang=lang)} "
            f"(TimeoutError): {_translate('msg_import_timeout', lang=lang)}"
        )
    elif diag.load_exception_type and not diag.loaded:
        lines.append(
            f"    {_translate('label_load_error', lang=lang)} "
            f"({diag.load_exception_type}): {diag.load_exception_message}"
        )

    if not diag.has_dependency_info and details:
        lines.append(f"    {_translate('msg_no_dep_info', lang=lang)}")

    return lines


def _render_suggestions_text(
    failed: list[PluginDiagnosis], lang: str
) -> list[str]:
    lines = []
    hint_py = _translate("hint_install_python_packages", lang=lang)
    hint_cmd = _translate("hint_install_external_commands", lang=lang)
    hint_ok = _translate("hint_all_deps_present", lang=lang)
    hint_unknown = _translate("hint_no_dep_info_available", lang=lang)

    for diag in sorted(failed, key=lambda d: d.name):
        if diag.has_dependency_info:
            if diag.missing_python_packages:
                lines.append(
                    f"  {diag.name}: {hint_py}: "
                    f"{', '.join(diag.missing_python_packages)}"
                )
            if diag.missing_external_commands:
                lines.append(
                    f"  {diag.name}: {hint_cmd}: "
                    f"{', '.join(diag.missing_external_commands)}"
                )
            if (
                not diag.missing_python_packages
                and not diag.missing_external_commands
            ):
                lines.append(f"  {diag.name}: {hint_ok}")
        else:
            lines.append(f"  {diag.name}: {hint_unknown}")
    return lines


def _format_text(
    diagnoses: list[PluginDiagnosis], details: bool, lang: str
) -> None:
    ui.print_(
        _translate(
            "section_header_environment",
            lang=lang,
            beets_ver=beets.__version__,
            py_ver=python_version(),
        )
    )
    ui.print_(f"{_translate('label_python_path', lang=lang)}: {sys.executable}")
    ui.print_("")

    configured_count = len(diagnoses)
    loaded_count = sum(1 for d in diagnoses if d.loaded)
    ui.print_(
        f"{_translate('label_plugins_configured', lang=lang)}: {configured_count}"
    )
    ui.print_(
        f"{_translate('label_plugins_loaded', lang=lang)}: {loaded_count}"
    )
    ui.print_("")

    if configured_count == 0:
        ui.print_(_translate("msg_no_plugins", lang=lang))
        return

    ui.print_(f"{_translate('section_plugin_status', lang=lang)}:")
    failed: list[PluginDiagnosis] = []
    for diag in diagnoses:
        if not diag.loaded:
            failed.append(diag)
        for line in _render_plugin_text(diag, details=details, lang=lang):
            ui.print_(line)

    if failed:
        ui.print_("")
        ui.print_(
            colorize(
                "text_error",
                f"{_translate('section_failed_to_load', lang=lang)}: ",
            )
            + ", ".join(sorted(d.name for d in failed))
        )
        ui.print_("")
        ui.print_(f"{_translate('section_suggestions', lang=lang)}:")
        for line in _render_suggestions_text(failed, lang=lang):
            ui.print_(line)


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


def _resolve_timeout(opts) -> float:
    raw = getattr(opts, "timeout", None)
    if raw is None:
        return DEFAULT_IMPORT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise UserError(f"invalid --timeout value {raw!r}: {exc}") from exc
    if value <= 0:
        raise UserError(f"--timeout must be > 0, got {value}")
    return value


def doctor_func(lib, opts, args):
    output_format: str = (getattr(opts, "format", "text") or "text").lower()
    if output_format not in ("text", "json"):
        raise UserError(
            _translate("error_unsupported_format", fmt=output_format)
        )
    show_details: bool = opts.details
    timeout: float = _resolve_timeout(opts)
    lang: str = _detect_language()

    configured = _get_configured_plugins()
    loaded = _get_loaded_plugin_names()
    diagnoses = [
        _diagnose_plugin(name, loaded, timeout=timeout)
        for name in sorted(configured)
    ]

    if output_format == "json":
        _format_json(diagnoses)
    else:
        _format_text(diagnoses, details=show_details, lang=lang)


doctor_cmd = ui.Subcommand("doctor", help=_translate("help_doctor"))
doctor_cmd.parser.add_option(
    "-d",
    "--details",
    action="store_true",
    default=False,
    help=_translate("help_details"),
)
doctor_cmd.parser.add_option(
    "-f",
    "--format",
    dest="format",
    default="text",
    help=_translate("help_format"),
)
doctor_cmd.parser.add_option(
    "-t",
    "--timeout",
    dest="timeout",
    default=str(DEFAULT_IMPORT_TIMEOUT_SECONDS),
    type=float,
    help=_translate("help_timeout"),
)
doctor_cmd.func = doctor_func

__all__ = [
    "PLUGIN_DEPENDENCIES",
    "DependencyInfo",
    "PluginDiagnosis",
    "_detect_language",
    "_diagnose_plugin",
    "_probe_import",
    "_translate",
    "doctor_cmd",
]
