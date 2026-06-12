import json
import re
from enum import Enum
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


def _repo_root() -> str:
    import pathlib

    return str(pathlib.Path(__file__).parents[3])


def _doc_path() -> str:
    import os

    return os.path.join(_repo_root(), "docs", "plugin-doctor.md")


def _read_doc() -> str:
    with open(_doc_path()) as f:
        return f.read()


def _extract_latest_version(doc_text: str) -> str:
    for line in doc_text.splitlines():
        line = line.strip()
        match = re.match(r"^##\s+(\d+\.\d+\.\d+)\s*$", line)
        if match:
            return match.group(1)
    raise AssertionError("No version heading (## x.y.z) found in doc")


def _extract_table_first_column(doc_text: str, heading: str) -> set[str]:
    lines = doc_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == heading:
            start = i
            break
    if start is None:
        raise AssertionError(f"Heading '{heading}' not found in doc")

    table_start = None
    for i in range(start + 1, len(lines)):
        line = lines[i].strip()
        if line.startswith("|"):
            table_start = i
            break
        if line.startswith("### ") or line.startswith("## "):
            break
    if table_start is None:
        raise AssertionError(f"No table found under heading '{heading}'")

    values: set[str] = set()
    header_skipped = False
    for i in range(table_start, len(lines)):
        line = lines[i].strip()
        if not line.startswith("|"):
            break
        if re.match(r"^\|[\s\-:]+\|", line):
            header_skipped = True
            continue
        if not header_skipped:
            header_skipped = True
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells:
            raw_value = cells[0]
            clean_value = raw_value.strip().strip("`")
            if clean_value:
                values.add(clean_value)
    return values


def _extract_enum_allowed_values(
    doc_text: str, heading: str, field_name: str
) -> set[str]:
    lines = doc_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == heading:
            start = i
            break
    if start is None:
        raise AssertionError(f"Heading '{heading}' not found in doc")

    table_start = None
    for i in range(start + 1, len(lines)):
        line = lines[i].strip()
        if line.startswith("|"):
            table_start = i
            break
        if line.startswith("### ") or line.startswith("## "):
            break
    if table_start is None:
        raise AssertionError(
            f"No table found under heading '{heading}'"
        )

    for i in range(table_start, len(lines)):
        line = lines[i].strip()
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        first = cells[0].strip().strip("`")
        if first != field_name:
            continue
        if len(cells) < 3:
            raise AssertionError(
                f"Row for '{field_name}' has fewer than 3 columns"
            )
        allowed_cell = cells[2]
        if allowed_cell.strip() == "*(any)*":
            return set()
        values: set[str] = set()
        for part in allowed_cell.split(","):
            val = part.strip().strip("`").strip()
            if val:
                values.add(val)
        return values

    raise AssertionError(
        f"Field '{field_name}' not found in table under '{heading}'"
    )


class TestDocSchemaConsistency:
    def test_doc_file_exists(self):
        import os

        assert os.path.isfile(_doc_path()), (
            f"Doc file not found at {_doc_path()}"
        )

    def test_doc_latest_version_matches_schema_version(self):
        doc = _read_doc()
        latest = _extract_latest_version(doc)
        assert latest == SCHEMA_VERSION, (
            f"Doc latest version '{latest}' does not match "
            f"SCHEMA_VERSION '{SCHEMA_VERSION}'. "
            f"Update the doc changelog or the SCHEMA_VERSION constant."
        )

    def test_doc_top_level_fields_match_fixture(self):
        doc = _read_doc()
        doc_fields = _extract_table_first_column(doc, "### Top-Level Object")
        assert doc_fields == TOP_LEVEL_FIELDS, (
            f"Top-level fields in doc ({sorted(doc_fields)}) "
            f"do not match fixture ({sorted(TOP_LEVEL_FIELDS)}). "
            f"Update the doc or TOP_LEVEL_FIELDS."
        )

    def test_doc_check_item_fields_match_fixture(self):
        doc = _read_doc()
        doc_fields = _extract_table_first_column(doc, "### Check Item Object")
        assert doc_fields == CHECK_ITEM_FIELDS, (
            f"Check item fields in doc ({sorted(doc_fields)}) "
            f"do not match fixture ({sorted(CHECK_ITEM_FIELDS)}). "
            f"Update the doc or CHECK_ITEM_FIELDS."
        )

    def test_doc_categories_match_fixture(self):
        doc = _read_doc()
        doc_cats = _extract_table_first_column(doc, "### Categories")
        assert doc_cats == VALID_CATEGORIES, (
            f"Categories in doc ({sorted(doc_cats)}) "
            f"do not match fixture ({sorted(VALID_CATEGORIES)}). "
            f"Update the doc or VALID_CATEGORIES."
        )

    def test_doc_statuses_match_fixture(self):
        doc = _read_doc()
        doc_statuses = _extract_table_first_column(doc, "### Statuses")
        assert doc_statuses == VALID_STATUSES, (
            f"Statuses in doc ({sorted(doc_statuses)}) "
            f"do not match fixture ({sorted(VALID_STATUSES)}). "
            f"Update the doc or VALID_STATUSES."
        )

    def test_doc_category_allowed_values_inline_match_code(self):
        doc = _read_doc()
        doc_vals = _extract_enum_allowed_values(
            doc, "### Check Item Object", "category"
        )
        assert doc_vals == VALID_CATEGORIES, (
            f"category allowed values in doc ({sorted(doc_vals)}) "
            f"do not match VALID_CATEGORIES ({sorted(VALID_CATEGORIES)}). "
            f"Update the doc or VALID_CATEGORIES."
        )

    def test_doc_status_allowed_values_inline_match_code(self):
        doc = _read_doc()
        doc_vals = _extract_enum_allowed_values(
            doc, "### Check Item Object", "status"
        )
        assert doc_vals == VALID_STATUSES, (
            f"status allowed values in doc ({sorted(doc_vals)}) "
            f"do not match VALID_STATUSES ({sorted(VALID_STATUSES)}). "
            f"Update the doc or VALID_STATUSES."
        )

    def test_doc_category_section_matches_code(self):
        doc = _read_doc()
        doc_cats = _extract_table_first_column(doc, "### Categories")
        assert doc_cats == VALID_CATEGORIES, (
            f"Categories section in doc ({sorted(doc_cats)}) "
            f"does not match VALID_CATEGORIES ({sorted(VALID_CATEGORIES)})."
        )

    def test_doc_status_section_matches_code(self):
        doc = _read_doc()
        doc_statuses = _extract_table_first_column(doc, "### Statuses")
        assert doc_statuses == VALID_STATUSES, (
            f"Statuses section in doc ({sorted(doc_statuses)}) "
            f"does not match VALID_STATUSES ({sorted(VALID_STATUSES)})."
        )

    def test_doc_category_inline_matches_category_section(self):
        doc = _read_doc()
        inline = _extract_enum_allowed_values(
            doc, "### Check Item Object", "category"
        )
        section = _extract_table_first_column(doc, "### Categories")
        assert inline == section, (
            f"Inline category values ({sorted(inline)}) "
            f"don't match Categories section ({sorted(section)})."
        )

    def test_doc_status_inline_matches_status_section(self):
        doc = _read_doc()
        inline = _extract_enum_allowed_values(
            doc, "### Check Item Object", "status"
        )
        section = _extract_table_first_column(doc, "### Statuses")
        assert inline == section, (
            f"Inline status values ({sorted(inline)}) "
            f"don't match Statuses section ({sorted(section)})."
        )


class TestRuntimeEnumEnforcement:
    def test_valid_category_passes_validation(self):
        for cat in CheckCategory:
            r = CheckResult("p", cat, CheckStatus.OK, "m")
            d = r.to_dict()
            assert d["category"] == cat.value

    def test_valid_status_passes_validation(self):
        for status in CheckStatus:
            r = CheckResult("p", CheckCategory.ENABLED, status, "m")
            d = r.to_dict()
            assert d["status"] == status.value

    def test_invalid_category_raises(self):
        r = CheckResult.__new__(CheckResult)
        r.plugin = "p"
        r.category = CheckCategory.ENABLED
        r.status = CheckStatus.OK
        r.message = "m"
        r.suggestion = ""

        class FakeCategory(Enum):
            INVALID = "nonexistent"

        r.category = FakeCategory.INVALID

        with pytest.raises(ValueError, match="Invalid category value"):
            r.to_dict()

    def test_invalid_status_raises(self):
        r = CheckResult.__new__(CheckResult)
        r.plugin = "p"
        r.category = CheckCategory.ENABLED
        r.status = CheckStatus.OK
        r.message = "m"
        r.suggestion = ""

        class FakeStatus(Enum):
            INVALID = "nonexistent"

        r.status = FakeStatus.INVALID

        with pytest.raises(ValueError, match="Invalid status value"):
            r.to_dict()

    def test_health_report_validates_checks_keys(self):
        r = CheckResult("p", CheckCategory.ENABLED, CheckStatus.OK, "m")
        report = HealthReport(results=[r])
        d = report.to_dict()
        for key in d["checks"]:
            assert key in VALID_CATEGORIES


class TestRuntimeEnumEnforcementCLI(IOMixin, PytestTestHelper):
    def test_cli_json_all_category_values_within_doc_set(self):
        out = self.run_with_output("check", "--format", "json")
        data = json.loads(out)
        for cat_name in data["checks"]:
            assert cat_name in VALID_CATEGORIES, (
                f"Category '{cat_name}' from CLI output "
                f"not in VALID_CATEGORIES {sorted(VALID_CATEGORIES)}"
            )
            for item in data["checks"][cat_name]:
                assert item["category"] in VALID_CATEGORIES, (
                    f"category value '{item['category']}' "
                    f"not in VALID_CATEGORIES"
                )

    def test_cli_json_all_status_values_within_doc_set(self):
        out = self.run_with_output("check", "--format", "json")
        data = json.loads(out)
        for cat_items in data["checks"].values():
            for item in cat_items:
                assert item["status"] in VALID_STATUSES, (
                    f"status value '{item['status']}' "
                    f"not in VALID_STATUSES"
                )
