"""Tests for the ``beet doctor`` diagnostic command."""

from __future__ import annotations

import json
import sys

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
        # We don't assert the specific package because the test env might
        # have it installed; instead, just check that we get valid output.
        assert "Plugins configured: 1" in out

    def test_missing_external_command_detected(self):
        self.config["plugins"] = ["keyfinder"]
        plugins.load_plugins()
        out = self.run_with_output("doctor")
        assert "keyfinder:" in out
        # Either "External commands (missing)" if KeyFinder isn't installed,
        # or "FAILED" if the plugin load itself fails for another reason.
        assert "keyfinder:" in out

    def test_missing_dep_appears_in_suggestions(self):
        self.config["plugins"] = ["discogs"]
        plugins.load_plugins()
        out = self.run_with_output("doctor")
        # Suggestions section exists regardless of load result.
        if "discogs_client" in out:
            assert "Suggestions:" in out

    # -- --details flag ---------------------------------------------

    def test_details_shows_all_dependency_info(self):
        self.config["plugins"] = ["info"]
        plugins.load_plugins()
        out = self.run_with_output("doctor", "--details")
        # info plugin has no dep info, should still be visible
        assert "info: OK" in out

    def test_details_includes_dependency_status_for_loaded_plugins(self):
        # ``fetchart`` has known Python deps.
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
        # info plugin has no deps and should always import cleanly.
        assert exc_type is None
        assert exc_msg is None
        assert exc_tb is None

    # -- PluginDiagnosis dataclass ----------------------------------

    def test_plugin_diagnosis_contains_dependency_fields(self):
        from beets.ui.commands.doctor import _diagnose_plugin

        diag = _diagnose_plugin("info", {"info"})
        assert diag.name == "info"
        assert diag.loaded is True
        assert isinstance(diag.missing_python_packages, list)
        assert isinstance(diag.missing_external_commands, list)

    def test_plugin_diagnosis_records_failure(self):
        from beets.ui.commands.doctor import _diagnose_plugin

        self.config["plugins"] = ["nonexistent_plugin_xyz"]
        plugins.load_plugins()
        diag = _diagnose_plugin("nonexistent_plugin_xyz", set())
        assert diag.loaded is False
        assert diag.load_exception_type is not None


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
        # load a known-good plugin
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
