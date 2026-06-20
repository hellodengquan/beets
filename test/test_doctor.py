"""Tests for the ``beet doctor`` diagnostic command."""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

from beets import plugins
from beets.test.helper import IOMixin, PytestTestHelper


@pytest.fixture
def capteesys(capsys):
    """Alias ``capsys`` to ``capteesys`` for environments where the
    ``pytest-capturelog`` plugin is not installed.

    The beets test suite only relies on the ``readouterr()`` method, which
    ``capsys`` provides, so this is a drop-in replacement.
    """
    return capsys


class TestDoctorCommand(IOMixin, PytestTestHelper):
    """Tests for the ``beet doctor`` CLI command."""

    @pytest.fixture(autouse=True)
    def _clean_plugins(self):
        plugins.clear_plugin_state()
        yield
        plugins.clear_plugin_state()
        for mod in list(sys.modules):
            if mod.startswith("beetsplug."):
                del sys.modules[mod]

    # -- basic output ------------------------------------------------

    def test_no_plugins_configured(self):
        out = self.run_with_output("doctor")
        assert "Plugins configured: 0" in out
        assert "No plugins configured." in out

    def test_shows_environment_header(self):
        out = self.run_with_output("doctor")
        assert "beets version" in out
        assert "Python version" in out
        assert "Python path:" in out

    def test_loaded_plugin_shown_ok(self):
        # ``info`` is a pure-Python plugin with no extra deps.
        self.config["plugins"] = ["info"]
        plugins.load_plugins()
        out = self.run_with_output("doctor")
        assert "info: OK" in out
        assert "Plugins configured: 1" in out
        assert "Plugins loaded: 1" in out

    # -- failed plugin diagnosis ------------------------------------

    def test_failed_plugin_shown_failed_with_error(self):
        self.config["plugins"] = ["nonexistent_plugin_xyz"]
        plugins.load_plugins()
        out = self.run_with_output("doctor")
        assert "nonexistent_plugin_xyz: FAILED" in out
        assert "Load error (PluginImportError)" in out
        assert "Failed to load:" in out
        assert "nonexistent_plugin_xyz" in out

    def test_plugin_load_failure_captured_in_registry(self):
        self.config["plugins"] = ["nonexistent_plugin_xyz"]
        plugins.load_plugins()
        failures = plugins.plugin_load_failures()
        assert len(failures) == 1
        assert failures[0].name == "nonexistent_plugin_xyz"
        assert failures[0].exception_type == "PluginImportError"

    def test_cause_exception_included_in_message(self):
        self.config["plugins"] = ["nonexistent_plugin_xyz"]
        plugins.load_plugins()
        out = self.run_with_output("doctor")
        assert "caused by" in out.lower() or "ModuleNotFoundError" in out

    # -- missing dependency detection -------------------------------

    def test_missing_python_package_detected(self):
        # ``discogs`` requires the ``discogs_client`` Python package.
        self.config["plugins"] = ["discogs"]
        plugins.load_plugins()
        out = self.run_with_output("doctor")
        assert "discogs:" in out
        assert "Plugins configured: 1" in out

    def test_missing_external_command_detected(self):
        self.config["plugins"] = ["keyfinder"]
        plugins.load_plugins()
        out = self.run_with_output("doctor")
        assert "keyfinder:" in out

    def test_missing_dep_appears_in_suggestions(self):
        self.config["plugins"] = ["discogs"]
        plugins.load_plugins()
        out = self.run_with_output("doctor")
        if "discogs_client" in out:
            assert "Suggestions:" in out

    # -- extended native dependency metadata ------------------------

    def test_chroma_includes_chromaprint_native_and_python(self):
        from beets.ui.commands.doctor import (
            PLUGIN_DEPENDENCIES,
            _diagnose_plugin,
        )

        dep = PLUGIN_DEPENDENCIES["chroma"]
        assert "chromaprint" in dep.python_packages
        assert "chromaprint" in dep.external_commands
        assert "fpcalc" in dep.external_commands
        assert "acoustid" in dep.python_packages

        self.config["plugins"] = ["chroma"]
        plugins.load_plugins()
        diag = _diagnose_plugin("chroma", set())
        assert diag.has_dependency_info is True
        assert "chromaprint" in diag.known_python_packages
        assert "chromaprint" in diag.known_external_commands

    def test_scrub_includes_mutagen_native_dep(self):
        from beets.ui.commands.doctor import (
            PLUGIN_DEPENDENCIES,
            _diagnose_plugin,
        )

        dep = PLUGIN_DEPENDENCIES["scrub"]
        assert "mutagen" in dep.python_packages

        diag = _diagnose_plugin("scrub", set())
        assert "mutagen" in diag.known_python_packages

    def test_dependency_info_populated_in_json(self):
        self.config["plugins"] = ["info"]
        plugins.load_plugins()
        out = self.run_with_output("doctor", "-f", "json")
        data = json.loads(out)
        diag = data["plugins"][0]
        assert "has_dependency_info" in diag
        assert "known_python_packages" in diag
        assert "known_external_commands" in diag
        assert isinstance(diag["known_python_packages"], list)
        assert isinstance(diag["known_external_commands"], list)

    def test_unknown_plugin_has_no_dependency_info(self):
        from beets.ui.commands.doctor import _diagnose_plugin

        diag = _diagnose_plugin("completely_unknown_plugin_42", set())
        assert diag.has_dependency_info is False
        assert diag.known_python_packages == []
        assert diag.known_external_commands == []

    # -- --details flag ---------------------------------------------

    def test_details_shows_all_dependency_info(self):
        self.config["plugins"] = ["info"]
        plugins.load_plugins()
        out = self.run_with_output("doctor", "--details")
        assert "info: OK" in out

    def test_details_includes_dependency_status_for_loaded_plugins(self):
        self.config["plugins"] = ["fetchart"]
        plugins.load_plugins()
        out = self.run_with_output("doctor", "--details")
        assert "fetchart:" in out

    # -- JSON output format -----------------------------------------

    def test_json_output_is_valid_and_has_expected_keys(self):
        self.config["plugins"] = ["info"]
        plugins.load_plugins()
        out = self.run_with_output("doctor", "--format", "json")
        data = json.loads(out)
        assert "beets_version" in data
        assert "python_version" in data
        assert "python_executable" in data
        assert "plugins_configured" in data
        assert "plugins_loaded" in data
        assert isinstance(data["plugins"], list)
        assert len(data["plugins"]) == 1
        assert data["plugins"][0]["name"] == "info"
        assert data["plugins"][0]["loaded"] is True
        assert data["plugins_configured"] == 1
        assert data["plugins_loaded"] == 1
        assert "has_dependency_info" in data["plugins"][0]

    def test_json_output_captures_failed_plugin(self):
        self.config["plugins"] = ["nonexistent_plugin_xyz"]
        plugins.load_plugins()
        out = self.run_with_output("doctor", "-f", "json")
        data = json.loads(out)
        assert len(data["plugins"]) == 1
        diag = data["plugins"][0]
        assert diag["name"] == "nonexistent_plugin_xyz"
        assert diag["loaded"] is False
        assert diag["load_exception_type"] is not None
        assert diag["load_exception_message"] is not None
        assert "PluginImportError" in diag["load_exception_type"]

    def test_json_output_f_flag_case_insensitive(self):
        self.config["plugins"] = []
        plugins.load_plugins()
        out = self.run_with_output("doctor", "--format", "JSON")
        data = json.loads(out)
        assert "plugins_configured" in data

    def test_invalid_format_raises_user_error(self):
        self.config["plugins"] = []
        plugins.load_plugins()
        from beets.exceptions import UserError

        with pytest.raises(UserError, match="unsupported output format"):
            self.run_command("doctor", "--format", "xml")

    # -- probe_import captures native-style exceptions --------------

    def test_probe_import_captures_import_failure(self):
        from beets.ui.commands.doctor import _probe_import

        exc_type, exc_msg, exc_tb = _probe_import("nonexistent_plugin_xyz")
        assert exc_type is not None
        assert exc_msg is not None
        assert exc_tb is not None
        assert (
            "PluginImportError" in exc_type or "ModuleNotFoundError" in exc_type
        )

    def test_probe_import_returns_none_for_good_plugin(self):
        from beets.ui.commands.doctor import _probe_import

        exc_type, exc_msg, exc_tb = _probe_import("info")
        assert exc_type is None
        assert exc_msg is None
        assert exc_tb is None

    def test_probe_import_handles_recursion_error_friendly(self):
        from beets.ui.commands.doctor import _probe_import

        with patch(
            "beets.ui.commands.doctor.importlib.import_module",
            side_effect=RecursionError("infinite loop"),
        ):
            exc_type, exc_msg, exc_tb = _probe_import("info")

        assert exc_type == "RecursionError"
        assert exc_msg is not None
        assert "fatal exception" in exc_msg.lower()
        assert exc_tb is None

    def test_probe_import_handles_memory_error_friendly(self):
        from beets.ui.commands.doctor import _probe_import

        with patch(
            "beets.ui.commands.doctor.importlib.import_module",
            side_effect=MemoryError("oom"),
        ):
            exc_type, exc_msg, exc_tb = _probe_import("info")

        assert exc_type == "MemoryError"
        assert "fatal exception" in exc_msg.lower()
        assert exc_tb is None

    def test_probe_import_handles_system_exit_friendly(self):
        from beets.ui.commands.doctor import _probe_import

        with patch(
            "beets.ui.commands.doctor.importlib.import_module",
            side_effect=SystemExit(1),
        ):
            exc_type, exc_msg, exc_tb = _probe_import("info")

        assert exc_type == "SystemExit"
        assert "fatal exception" in exc_msg.lower()
        assert exc_tb is None

    def test_probe_import_no_traceback_for_fatal_exceptions(self):
        from beets.ui.commands.doctor import _probe_import

        for exc_class in (
            RecursionError,
            MemoryError,
            KeyboardInterrupt,
            SystemExit,
        ):
            with patch(
                "beets.ui.commands.doctor.importlib.import_module",
                side_effect=exc_class("boom"),
            ):
                _, _, exc_tb = _probe_import("info")
                assert exc_tb is None

    # -- PluginDiagnosis dataclass ----------------------------------

    def test_plugin_diagnosis_contains_dependency_fields(self):
        from beets.ui.commands.doctor import _diagnose_plugin

        diag = _diagnose_plugin("info", {"info"})
        assert diag.name == "info"
        assert diag.loaded is True
        assert isinstance(diag.missing_python_packages, list)
        assert isinstance(diag.missing_external_commands, list)
        assert isinstance(diag.has_dependency_info, bool)
        assert isinstance(diag.known_python_packages, list)
        assert isinstance(diag.known_external_commands, list)

    def test_plugin_diagnosis_records_failure(self):
        from beets.ui.commands.doctor import _diagnose_plugin

        self.config["plugins"] = ["nonexistent_plugin_xyz"]
        plugins.load_plugins()
        diag = _diagnose_plugin("nonexistent_plugin_xyz", set())
        assert diag.loaded is False
        assert diag.load_exception_type is not None

    def test_plugin_diagnosis_includes_known_deps(self):
        from beets.ui.commands.doctor import _diagnose_plugin

        diag = _diagnose_plugin("chroma", set())
        assert "acoustid" in diag.known_python_packages
        assert "chromaprint" in diag.known_python_packages
        assert "fpcalc" in diag.known_external_commands
        assert "chromaprint" in diag.known_external_commands
        assert diag.has_dependency_info is True

    # -- unified rendering ------------------------------------------

    def test_text_and_json_output_share_same_diagnosis_data(self):
        self.config["plugins"] = ["discogs", "info", "nonexistent_plugin_xyz"]
        plugins.load_plugins()

        text_out = self.run_with_output("doctor")
        json_out = self.run_with_output("doctor", "-f", "json")
        data = json.loads(json_out)

        assert data["plugins_configured"] == 3
        assert f"Plugins configured: {data['plugins_configured']}" in text_out
        assert f"Plugins loaded: {data['plugins_loaded']}" in text_out

        plugin_names = {d["name"] for d in data["plugins"]}
        assert plugin_names == {"discogs", "info", "nonexistent_plugin_xyz"}

        for diag in data["plugins"]:
            if diag["loaded"]:
                assert f"{diag['name']}: OK" in text_out
            else:
                assert f"{diag['name']}: FAILED" in text_out
                if diag["load_exception_message"]:
                    assert diag["load_exception_type"] in text_out


class TestPluginLoadFailureAPI(PytestTestHelper):
    """Tests for the plugin load-failure tracking APIs."""

    @pytest.fixture(autouse=True)
    def _clean(self):
        plugins.clear_plugin_state()
        yield
        plugins.clear_plugin_state()

    def test_load_failures_list_empty_initially(self):
        assert plugins.plugin_load_failures() == []

    def test_clear_plugin_state_resets_everything(self):
        self.config["plugins"] = ["info"]
        plugins.load_plugins()
        assert len(list(plugins.find_plugins())) >= 1

        plugins.clear_plugin_state()
        assert list(plugins.find_plugins()) == []
        assert plugins.plugin_load_failures() == []

    def test_failure_recorded_with_exception_details(self):
        self.config["plugins"] = ["definitely_not_a_real_plugin_404"]
        plugins.load_plugins()
        failures = plugins.plugin_load_failures()
        assert len(failures) == 1
        failure = failures[0]
        assert failure.name == "definitely_not_a_real_plugin_404"
        assert failure.exception_type == "PluginImportError"
        assert isinstance(failure.exception_message, str)
        assert failure.exception_message  # non-empty
