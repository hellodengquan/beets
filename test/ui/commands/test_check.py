import json
import re
from unittest.mock import patch

import pytest

from beets.test.helper import IOMixin, PytestTestHelper
from beets.ui.commands.check import (
    CHECK_ITEM_FIELDS,
    SCHEMA_VERSION,
    TOP_LEVEL_FIELDS,
    VALID_CATEGORIES,
    VALID_STATUSES,
    CheckCategory,
    CheckResult,
    CheckStatus,
    HealthReport,
    _check_config,
    _check_dependencies,
    _check_enabled,
    _collect_results,
)

SEMVER_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

STABLE_SCHEMA_FIXTURE: dict[str, object] = {
    "schema_version": "1.0.0",
    "top_level_fields": {
        "schema_version",
        "ok_count",
        "warning_count",
        "error_count",
        "has_errors",
        "checks",
    },
    "check_item_fields": {
        "plugin",
        "category",
        "status",
        "message",
        "suggestion",
    },
    "valid_categories": {"enabled", "dependency", "command", "config"},
    "valid_statuses": {"ok", "warning", "error"},
}


@pytest.fixture(scope="module")
def schema_stability_fixture():
    return STABLE_SCHEMA_FIXTURE


class TestCheckResultDataclass:
    def test_to_dict_keys(self):
        r = CheckResult(
            plugin="test",
            category=CheckCategory.ENABLED,
            status=CheckStatus.OK,
            message="msg",
            suggestion="fix",
        )
        d = r.to_dict()
        assert set(d.keys()) == {
            "plugin",
            "category",
            "status",
            "message",
            "suggestion",
        }

    def test_to_dict_enum_values(self):
        r = CheckResult(
            plugin="p",
            category=CheckCategory.DEPENDENCY,
            status=CheckStatus.WARNING,
            message="m",
        )
        d = r.to_dict()
        assert d["category"] == "dependency"
        assert d["status"] == "warning"
        assert d["suggestion"] == ""

    def test_all_categories_serializable(self):
        for cat in CheckCategory:
            r = CheckResult("p", cat, CheckStatus.OK, "m")
            d = r.to_dict()
            assert d["category"] == cat.value

    def test_all_statuses_serializable(self):
        for status in CheckStatus:
            r = CheckResult("p", CheckCategory.ENABLED, status, "m")
            d = r.to_dict()
            assert d["status"] == status.value


class TestHealthReport:
    def test_empty_report(self):
        report = HealthReport()
        assert report.ok_count == 0
        assert report.warning_count == 0
        assert report.error_count == 0
        assert not report.has_errors

    def test_counts(self):
        results = [
            CheckResult("a", CheckCategory.ENABLED, CheckStatus.OK, "ok"),
            CheckResult("b", CheckCategory.ENABLED, CheckStatus.WARNING, "w"),
            CheckResult("c", CheckCategory.ENABLED, CheckStatus.ERROR, "e"),
            CheckResult("d", CheckCategory.ENABLED, CheckStatus.OK, "ok2"),
        ]
        report = HealthReport(results=results)
        assert report.ok_count == 2
        assert report.warning_count == 1
        assert report.error_count == 1
        assert report.has_errors

    def test_to_dict_top_level_schema(self):
        report = HealthReport(results=[
            CheckResult("p", CheckCategory.ENABLED, CheckStatus.OK, "m"),
        ])
        d = report.to_dict()
        assert set(d.keys()) == {
            "schema_version",
            "ok_count",
            "warning_count",
            "error_count",
            "has_errors",
            "checks",
        }
        assert d["schema_version"] == SCHEMA_VERSION

    def test_to_dict_checks_grouped_by_category(self):
        results = [
            CheckResult("a", CheckCategory.ENABLED, CheckStatus.OK, "m1"),
            CheckResult("b", CheckCategory.DEPENDENCY, CheckStatus.ERROR, "m2", "fix"),
            CheckResult("c", CheckCategory.ENABLED, CheckStatus.WARNING, "m3"),
        ]
        report = HealthReport(results=results)
        d = report.to_dict()

        assert "enabled" in d["checks"]
        assert "dependency" in d["checks"]
        assert len(d["checks"]["enabled"]) == 2
        assert len(d["checks"]["dependency"]) == 1

    def test_to_json_produces_valid_json(self):
        report = HealthReport(results=[
            CheckResult("p", CheckCategory.CONFIG, CheckStatus.ERROR, "bad", "fix"),
        ])
        parsed = json.loads(report.to_json())
        assert parsed["error_count"] == 1
        assert parsed["checks"]["config"][0]["suggestion"] == "fix"

    def test_empty_categories_omitted(self):
        report = HealthReport(results=[
            CheckResult("p", CheckCategory.ENABLED, CheckStatus.OK, "m"),
        ])
        d = report.to_dict()
        assert "enabled" in d["checks"]
        assert "dependency" not in d["checks"]
        assert "command" not in d["checks"]
        assert "config" not in d["checks"]

    def test_each_check_item_has_required_fields(self):
        required_keys = {"plugin", "category", "status", "message", "suggestion"}
        report = HealthReport(results=[
            CheckResult("a", CheckCategory.COMMAND, CheckStatus.OK, "m", "s"),
            CheckResult("b", CheckCategory.CONFIG, CheckStatus.ERROR, "m2"),
        ])
        d = report.to_dict()
        for cat_items in d["checks"].values():
            for item in cat_items:
                assert set(item.keys()) == required_keys

    def test_schema_version_field_exists(self):
        report = HealthReport()
        d = report.to_dict()
        assert "schema_version" in d

    def test_schema_version_is_semver_string(self):
        report = HealthReport()
        d = report.to_dict()
        version = d["schema_version"]
        assert isinstance(version, str)
        assert SEMVER_PATTERN.match(version) is not None

    def test_schema_version_matches_module_constant(self):
        report = HealthReport()
        d = report.to_dict()
        assert d["schema_version"] == SCHEMA_VERSION

    def test_schema_version_module_constant_is_semver(self):
        assert isinstance(SCHEMA_VERSION, str)
        assert SEMVER_PATTERN.match(SCHEMA_VERSION) is not None


class TestSchemaStability:
    def test_top_level_fields_unchanged(self, schema_stability_fixture):
        expected = schema_stability_fixture["top_level_fields"]
        assert TOP_LEVEL_FIELDS == frozenset(expected)

    def test_check_item_fields_unchanged(self, schema_stability_fixture):
        expected = schema_stability_fixture["check_item_fields"]
        assert CHECK_ITEM_FIELDS == frozenset(expected)

    def test_valid_categories_unchanged(self, schema_stability_fixture):
        expected = schema_stability_fixture["valid_categories"]
        assert VALID_CATEGORIES == frozenset(expected)

    def test_valid_statuses_unchanged(self, schema_stability_fixture):
        expected = schema_stability_fixture["valid_statuses"]
        assert VALID_STATUSES == frozenset(expected)

    def test_schema_version_matches_fixture(self, schema_stability_fixture):
        assert SCHEMA_VERSION == schema_stability_fixture["schema_version"]

    def test_health_report_top_level_keys_exact_match(self, schema_stability_fixture):
        report = HealthReport(results=[
            CheckResult("p", CheckCategory.ENABLED, CheckStatus.OK, "m"),
        ])
        d = report.to_dict()
        assert set(d.keys()) == schema_stability_fixture["top_level_fields"]

    def test_health_report_check_item_keys_exact_match(self, schema_stability_fixture):
        report = HealthReport(results=[
            CheckResult("a", CheckCategory.COMMAND, CheckStatus.OK, "m", "s"),
        ])
        d = report.to_dict()
        for cat_items in d["checks"].values():
            for item in cat_items:
                assert set(item.keys()) == schema_stability_fixture[
                    "check_item_fields"
                ]


class TestSchemaStabilityCLI(IOMixin, PytestTestHelper):
    def test_cli_json_output_top_level_keys_exact_match(
        self, schema_stability_fixture
    ):
        out = self.run_with_output("check", "--format", "json")
        data = json.loads(out)
        assert set(data.keys()) == schema_stability_fixture["top_level_fields"]

    def test_cli_json_check_item_keys_exact_match(self, schema_stability_fixture):
        out = self.run_with_output("check", "--format", "json")
        data = json.loads(out)
        for cat_items in data["checks"].values():
            for item in cat_items:
                assert set(item.keys()) == schema_stability_fixture[
                    "check_item_fields"
                ]

    def test_cli_json_category_values_match_fixture(
        self, schema_stability_fixture
    ):
        out = self.run_with_output("check", "--format", "json")
        data = json.loads(out)
        expected = schema_stability_fixture["valid_categories"]
        for cat_name in data["checks"]:
            assert cat_name in expected
            for item in data["checks"][cat_name]:
                assert item["category"] in expected

    def test_cli_json_status_values_match_fixture(
        self, schema_stability_fixture
    ):
        out = self.run_with_output("check", "--format", "json")
        data = json.loads(out)
        expected = schema_stability_fixture["valid_statuses"]
        for cat_items in data["checks"].values():
            for item in cat_items:
                assert item["status"] in expected


class TestCheckEnabled:
    def test_loaded_plugin(self):
        results = _check_enabled(["musicbrainz"], set(), {"musicbrainz"})
        assert len(results) == 1
        assert results[0].status == CheckStatus.OK

    def test_disabled_plugin(self):
        results = _check_enabled(
            ["chroma"], {"chroma"}, {"musicbrainz"}
        )
        assert len(results) == 1
        assert results[0].status == CheckStatus.WARNING
        assert results[0].suggestion

    def test_failed_to_load(self):
        results = _check_enabled(["nonexistent"], set(), set())
        assert len(results) == 1
        assert results[0].status == CheckStatus.ERROR
        assert results[0].suggestion

    def test_no_plugins_configured(self):
        results = _check_enabled([], set(), set())
        assert results == []


class TestCheckDependencies:
    def test_no_extra_deps(self):
        results = _check_dependencies(["bucket"], {"bucket"})
        assert len(results) == 1
        assert results[0].status == CheckStatus.OK
        assert "no extra dependencies" in results[0].message

    def test_missing_deps(self):
        with patch(
            "beets.ui.commands.check._is_package_available",
            return_value=False,
        ):
            results = _check_dependencies(["chroma"], {"chroma"})
            assert len(results) == 1
            assert results[0].status == CheckStatus.ERROR
            assert results[0].suggestion

    def test_satisfied_deps(self):
        with patch(
            "beets.ui.commands.check._is_package_available",
            return_value=True,
        ):
            results = _check_dependencies(["chroma"], {"chroma"})
            assert len(results) == 1
            assert results[0].status == CheckStatus.OK

    def test_unloaded_plugin_skipped(self):
        results = _check_dependencies(["chroma"], set())
        assert results == []


class TestCheckConfig:
    def test_valid_config(self):
        results = _check_config({"musicbrainz"})
        assert any(
            r.status == CheckStatus.OK and r.category == CheckCategory.CONFIG
            for r in results
        )


class TestCollectResults:
    def test_collects_enabled_dependency_and_config(self):
        report = _collect_results(
            ["bucket"], set(), {"bucket"}
        )
        categories = {r.category for r in report.results}
        assert CheckCategory.ENABLED in categories
        assert CheckCategory.DEPENDENCY in categories
        assert CheckCategory.CONFIG in categories

    def test_command_category_for_plugin_with_commands(self):
        from beets.plugins import BeetsPlugin

        class FakePlugin(BeetsPlugin):
            def __init__(self):
                super().__init__("fake_for_test_check")
                from beets.ui import Subcommand

                self._cmd = Subcommand("fake_cmd_test")

            def commands(self):
                return [self._cmd]

        fake = FakePlugin()
        with patch(
            "beets.ui.commands.check.plugins.find_plugins",
            return_value=[fake],
        ):
            report = _collect_results(
                ["fake_for_test_check"],
                set(),
                {"fake_for_test_check"},
            )
            categories = {r.category for r in report.results}
            assert CheckCategory.COMMAND in categories

    def test_filter_plugins(self):
        report = _collect_results(
            ["bucket", "chroma"], set(), {"bucket"},
            filter_plugins={"bucket"},
        )
        plugin_names = {r.plugin for r in report.results}
        assert plugin_names == {"bucket"}

    def test_report_json_round_trip(self):
        report = _collect_results(
            ["bucket"], set(), {"bucket"}
        )
        parsed = json.loads(report.to_json())
        assert parsed["schema_version"] == SCHEMA_VERSION
        assert isinstance(parsed["ok_count"], int)
        assert isinstance(parsed["warning_count"], int)
        assert isinstance(parsed["error_count"], int)
        assert isinstance(parsed["has_errors"], bool)


class TestCheckCommandCLI(IOMixin, PytestTestHelper):
    def test_text_output_contains_summary(self):
        out = self.run_with_output("check")
        assert "Plugin Health Check" in out
        assert "Summary:" in out

    def test_json_output_is_valid(self):
        out = self.run_with_output("check", "--format", "json")
        data = json.loads(out)
        assert data["schema_version"] == SCHEMA_VERSION
        assert isinstance(data["ok_count"], int)
        assert isinstance(data["warning_count"], int)
        assert isinstance(data["error_count"], int)
        assert isinstance(data["has_errors"], bool)
        assert isinstance(data["checks"], dict)

    def test_json_output_has_all_required_top_level_keys(self):
        out = self.run_with_output("check", "--format", "json")
        data = json.loads(out)
        required = {
            "schema_version",
            "ok_count",
            "warning_count",
            "error_count",
            "has_errors",
            "checks",
        }
        assert set(data.keys()) == required

    def test_json_each_check_item_fields(self):
        out = self.run_with_output("check", "--format", "json")
        data = json.loads(out)
        required_keys = {"plugin", "category", "status", "message", "suggestion"}
        for cat_items in data["checks"].values():
            for item in cat_items:
                assert set(item.keys()) == required_keys

    def test_json_status_values_are_valid(self):
        out = self.run_with_output("check", "--format", "json")
        data = json.loads(out)
        valid_statuses = {"ok", "warning", "error"}
        for cat_items in data["checks"].values():
            for item in cat_items:
                assert item["status"] in valid_statuses

    def test_json_category_values_are_valid(self):
        out = self.run_with_output("check", "--format", "json")
        data = json.loads(out)
        valid_categories = {"enabled", "dependency", "command", "config"}
        for cat_name in data["checks"]:
            assert cat_name in valid_categories
            for item in data["checks"][cat_name]:
                assert item["category"] in valid_categories

    def test_json_and_text_consistent_plugin_names(self):
        text_out = self.run_with_output("check")
        json_out = self.run_with_output("check", "--format", "json")
        data = json.loads(json_out)

        json_plugins = set()
        for cat_items in data["checks"].values():
            for item in cat_items:
                json_plugins.add(item["plugin"])

        for plugin_name in json_plugins:
            assert plugin_name in text_out

    def test_json_counts_match_results(self):
        out = self.run_with_output("check", "--format", "json")
        data = json.loads(out)
        total_ok = 0
        total_warn = 0
        total_error = 0
        for cat_items in data["checks"].values():
            for item in cat_items:
                if item["status"] == "ok":
                    total_ok += 1
                elif item["status"] == "warning":
                    total_warn += 1
                elif item["status"] == "error":
                    total_error += 1
        assert data["ok_count"] == total_ok
        assert data["warning_count"] == total_warn
        assert data["error_count"] == total_error

    def test_json_has_errors_flag(self):
        out = self.run_with_output("check", "--format", "json")
        data = json.loads(out)
        assert data["has_errors"] == (data["error_count"] > 0)

    def test_doctor_alias(self):
        out = self.run_with_output("doctor")
        assert "Plugin Health Check" in out

    def test_json_short_flag(self):
        out = self.run_with_output("check", "-f", "json")
        data = json.loads(out)
        assert data["schema_version"] == SCHEMA_VERSION

    def test_json_suggestion_field_present(self):
        out = self.run_with_output("check", "--format", "json")
        data = json.loads(out)
        for cat_items in data["checks"].values():
            for item in cat_items:
                assert "suggestion" in item
