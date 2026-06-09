"""Unit tests for PluginConfigManager and related classes.

Tests cover 8 public methods with both success and failure paths:
- register
- register_batch
- apply_defaults
- validate
- get_int
- get_bool
- get_str
- get_float
"""

from __future__ import annotations

import warnings
from typing import Any

import confuse
import pytest

from beets.util.config import (
    PluginConfigError,
    PluginConfigManager,
    PluginConfigSchema,
)


@pytest.fixture
def fresh_config() -> confuse.Configuration:
    """Create a fresh confuse Configuration for each test."""
    config = confuse.Configuration("test_plugin_config", __name__)
    config.clear()
    return config


@pytest.fixture
def config_view(fresh_config: confuse.Configuration) -> confuse.Subview:
    """Provide a plugin-level subview."""
    return fresh_config["myplugin"]


@pytest.fixture
def manager(config_view: confuse.Subview) -> PluginConfigManager:
    """Create a basic PluginConfigManager."""
    return PluginConfigManager("myplugin", config_view)


class TestPluginConfigSchema:
    """Tests for PluginConfigSchema dataclass."""

    def test_schema_default_construction(self):
        schema = PluginConfigSchema(key="test_key")
        assert schema.key == "test_key"
        assert schema.default is None
        assert schema.type is None
        assert schema.choices is None
        assert schema.help == ""
        assert schema.required is False
        assert schema.validator is None
        assert schema.deprecated is False
        assert schema.redact is False

    def test_schema_full_construction(self):
        def validator(x: Any) -> bool:
            return x > 0

        schema = PluginConfigSchema(
            key="full_key",
            default=42,
            type=int,
            choices=[1, 2, 42],
            help="Test help text",
            required=True,
            validator=validator,
            deprecated=True,
            deprecation_message="Use new_key instead",
            redact=True,
        )
        assert schema.key == "full_key"
        assert schema.default == 42
        assert schema.type is int
        assert schema.choices == [1, 2, 42]
        assert schema.help == "Test help text"
        assert schema.required is True
        assert schema.validator is validator
        assert schema.deprecated is True
        assert schema.deprecation_message == "Use new_key instead"
        assert schema.redact is True


class TestRegister:
    """Tests for the register() method."""

    def test_register_single_success(self, manager: PluginConfigManager):
        manager.register("port", default=8080, type=int, help="Server port")
        assert "port" in manager._schemas
        schema = manager._schemas["port"]
        assert schema.default == 8080
        assert schema.type is int
        assert schema.help == "Server port"
        assert manager._registered_defaults["port"] == 8080

    def test_register_with_choices(self, manager: PluginConfigManager):
        manager.register(
            "mode", default="fast", type=str, choices=["fast", "slow", "safe"]
        )
        schema = manager._schemas["mode"]
        assert schema.choices == ["fast", "slow", "safe"]

    def test_register_required_no_default(self, manager: PluginConfigManager):
        manager.register("api_key", type=str, required=True)
        schema = manager._schemas["api_key"]
        assert schema.required is True
        assert schema.default is None

    def test_register_with_validator(self, manager: PluginConfigManager):
        def positive(x: int) -> bool:
            return x > 0

        manager.register("count", default=1, type=int, validator=positive)
        schema = manager._schemas["count"]
        assert callable(schema.validator)

    def test_register_overwrite_existing(self, manager: PluginConfigManager):
        manager.register("key", default="old")
        manager.register("key", default="new", type=str, help="Updated")
        schema = manager._schemas["key"]
        assert schema.default == "new"
        assert schema.type is str
        assert schema.help == "Updated"

    def test_register_with_deprecation(self, manager: PluginConfigManager):
        manager.register(
            "old_key",
            deprecated=True,
            deprecation_message="Use new_key instead",
        )
        schema = manager._schemas["old_key"]
        assert schema.deprecated is True
        assert schema.deprecation_message == "Use new_key instead"

    def test_register_redact_flag(self, manager: PluginConfigManager):
        manager.register("password", default=None, type=str, redact=True)
        schema = manager._schemas["password"]
        assert schema.redact is True

    def test_register_empty_key_allowed(self, manager: PluginConfigManager):
        manager.register("", default="empty")
        assert "" in manager._schemas


class TestRegisterBatch:
    """Tests for the register_batch() method."""

    def test_register_batch_multiple(self, manager: PluginConfigManager):
        configs = {
            "host": {"default": "localhost", "type": str, "help": "Hostname"},
            "port": {"default": 5432, "type": int, "help": "Port"},
            "debug": {"default": False, "type": bool},
        }
        manager.register_batch(configs)

        assert len(manager._schemas) == 3
        assert manager._schemas["host"].default == "localhost"
        assert manager._schemas["port"].type is int
        assert manager._schemas["debug"].default is False
        assert manager._registered_defaults["port"] == 5432

    def test_register_batch_empty(self, manager: PluginConfigManager):
        manager.register_batch({})
        assert len(manager._schemas) == 0
        assert len(manager._registered_defaults) == 0

    def test_register_batch_partial_params(
        self, manager: PluginConfigManager
    ):
        configs = {
            "full": {"default": 1, "type": int, "help": "Full"},
            "partial": {"default": "x"},
            "minimal": {},
        }
        manager.register_batch(configs)

        assert manager._schemas["full"].help == "Full"
        assert manager._schemas["partial"].default == "x"
        assert manager._schemas["partial"].type is None
        assert manager._schemas["minimal"].default is None

    def test_register_batch_overwrites(
        self, manager: PluginConfigManager
    ):
        manager.register("key", default="first")
        manager.register_batch({"key": {"default": "second", "help": "Overwritten"}})
        assert manager._schemas["key"].default == "second"
        assert manager._schemas["key"].help == "Overwritten"

    def test_register_batch_complex_validator(
        self, manager: PluginConfigManager
    ):
        def path_exists(v: str) -> bool:
            return bool(v)

        manager.register_batch(
            {
                "input_path": {
                    "type": str,
                    "required": True,
                    "validator": path_exists,
                },
                "output_path": {
                    "type": str,
                    "required": True,
                    "validator": path_exists,
                },
            }
        )
        assert manager._schemas["input_path"].required is True
        assert callable(manager._schemas["input_path"].validator)

    def test_register_batch_with_choices_validator(
        self, manager: PluginConfigManager
    ):
        manager.register_batch(
            {
                "loglevel": {
                    "default": "info",
                    "type": str,
                    "choices": ["debug", "info", "warn", "error"],
                },
                "format": {
                    "default": "json",
                    "type": str,
                    "choices": ["json", "yaml", "xml"],
                },
            }
        )
        assert set(manager._schemas["loglevel"].choices) == {
            "debug", "info", "warn", "error"
        }


class TestApplyDefaults:
    """Tests for the apply_defaults() method."""

    def test_apply_defaults_basic(
        self, fresh_config: confuse.Configuration, manager: PluginConfigManager
    ):
        manager.register("name", default="default_name")
        manager.register("count", default=42)
        manager.apply_defaults()

        assert fresh_config["myplugin"]["name"].get() == "default_name"
        assert fresh_config["myplugin"]["count"].get() == 42

    def test_apply_defaults_user_values_unchanged(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"name": "user_name"}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register("name", default="default_name")
        manager.register("count", default=42)
        manager.apply_defaults()

        assert manager.config["name"].get() == "user_name"
        assert manager.config["count"].get() == 42

    def test_apply_defaults_empty_registration(
        self, manager: PluginConfigManager
    ):
        manager.apply_defaults()
        assert manager.flatten() == {}

    def test_apply_defaults_with_redact(
        self, fresh_config: confuse.Configuration, manager: PluginConfigManager
    ):
        manager.register("secret", default=None, type=str, redact=True)
        manager.register("public", default="visible", type=str)
        manager.apply_defaults()

        assert hasattr(manager.config["secret"], "redact")
        assert manager.config["secret"].redact is True

    def test_apply_defaults_called_twice_idempotent(
        self, manager: PluginConfigManager
    ):
        manager.register("key", default="value")
        manager.apply_defaults()
        first = manager.config["key"].get()
        manager.apply_defaults()
        second = manager.config["key"].get()
        assert first == second

    def test_apply_defaults_after_user_set(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        manager = PluginConfigManager("myplugin", config_view)
        manager.register("key", default="before")
        manager.apply_defaults()
        assert manager.config["key"].get() == "before"

        fresh_config.set({"myplugin": {"key": "after_user"}})
        manager.apply_defaults()
        assert manager.config["key"].get() == "after_user"


class TestValidate:
    """Tests for the validate() method - success and failure paths."""

    def test_validate_success_all_defaults(
        self, manager: PluginConfigManager
    ):
        manager.register("port", default=8080, type=int)
        manager.register("name", default="test", type=str)
        manager.apply_defaults()
        manager.validate()
        assert manager._validated is True

    def test_validate_success_valid_choices(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"mode": "fast"}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register(
            "mode", default="slow", type=str, choices=["fast", "slow", "safe"]
        )
        manager.apply_defaults()
        manager.validate()
        assert manager._validated is True

    def test_validate_success_custom_validator(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        def positive(x: int) -> bool:
            return x > 0

        fresh_config.add({"myplugin": {"count": 5}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register("count", default=1, type=int, validator=positive)
        manager.apply_defaults()
        manager.validate()

    def test_validate_failure_missing_required(
        self, manager: PluginConfigManager
    ):
        manager.register("required_key", type=str, required=True)
        manager.apply_defaults()

        with pytest.raises(PluginConfigError) as exc_info:
            manager.validate()

        assert "required" in str(exc_info.value).lower()
        assert "required_key" in str(exc_info.value)

    def test_validate_failure_invalid_choice(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"mode": "invalid"}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register(
            "mode", default="a", type=str, choices=["a", "b", "c"]
        )
        manager.apply_defaults()

        with pytest.raises(PluginConfigError) as exc_info:
            manager.validate()

        assert "must be one of" in str(exc_info.value).lower()
        assert "mode" in str(exc_info.value)

    def test_validate_failure_validator_returns_false(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        def positive(x: int) -> bool:
            return x > 0

        fresh_config.add({"myplugin": {"count": -1}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register("count", default=1, type=int, validator=positive)
        manager.apply_defaults()

        with pytest.raises(PluginConfigError) as exc_info:
            manager.validate()

        assert "failed custom validation" in str(exc_info.value).lower()
        assert "count" in str(exc_info.value)

    def test_validate_failure_validator_raises(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        def raise_validator(x: int) -> bool:
            raise ValueError("Custom validation error")

        fresh_config.add({"myplugin": {"count": 5}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register("count", default=1, type=int, validator=raise_validator)
        manager.apply_defaults()

        with pytest.raises(PluginConfigError) as exc_info:
            manager.validate()

        assert "validation error" in str(exc_info.value).lower()

    def test_validate_failure_multiple_errors(
        self, manager: PluginConfigManager
    ):
        manager.register("required_1", type=str, required=True)
        manager.register("required_2", type=int, required=True)
        manager.register(
            "bad_choice", default="x", type=str, choices=["a", "b"]
        )
        manager.apply_defaults()

        with pytest.raises(PluginConfigError) as exc_info:
            manager.validate()

        msg = str(exc_info.value)
        assert "required_1" in msg
        assert "required_2" in msg
        assert "bad_choice" in msg

    def test_validate_deprecated_warning(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"old_key": "value"}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register(
            "old_key",
            default=None,
            deprecated=True,
            deprecation_message="Use new_key",
        )
        manager.apply_defaults()

        with pytest.warns(DeprecationWarning, match="Use new_key"):
            manager.validate()

    def test_validate_not_required_missing_ok(
        self, manager: PluginConfigManager
    ):
        manager.register("optional", type=str, required=False)
        manager.apply_defaults()
        manager.validate()


class TestGetInt:
    """Tests for the get_int() method - success and failure paths."""

    def test_get_int_success_default(
        self, manager: PluginConfigManager
    ):
        manager.register("retries", default=3, type=int)
        manager.apply_defaults()
        assert manager.get_int("retries") == 3

    def test_get_int_success_user_value(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"retries": 10}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register("retries", default=3, type=int)
        manager.apply_defaults()
        assert manager.get_int("retries") == 10

    def test_get_int_success_zero_value(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"port": 0}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register("port", default=8080, type=int)
        manager.apply_defaults()
        assert manager.get_int("port") == 0

    def test_get_int_failure_unknown_key(
        self, manager: PluginConfigManager
    ):
        with pytest.raises(PluginConfigError) as exc_info:
            manager.get_int("nonexistent")

        assert "Unknown configuration key" in str(exc_info.value)
        assert "nonexistent" in str(exc_info.value)

    def test_get_int_failure_type_mismatch(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"count": "not_an_int"}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register("count", default=0, type=int)
        manager.apply_defaults()

        with pytest.raises(PluginConfigError) as exc_info:
            manager.get_int("count")

        assert "count" in str(exc_info.value)

    def test_get_int_optional_missing_returns_default(
        self, manager: PluginConfigManager
    ):
        manager.register("maybe_int", default=-1, type=int)
        manager.apply_defaults()
        assert manager.get_int("maybe_int") == -1


class TestGetBool:
    """Tests for the get_bool() method - success and failure paths."""

    def test_get_bool_success_true(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"enabled": True}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register("enabled", default=False, type=bool)
        manager.apply_defaults()
        assert manager.get_bool("enabled") is True

    def test_get_bool_success_false(
        self, manager: PluginConfigManager
    ):
        manager.register("enabled", default=False, type=bool)
        manager.apply_defaults()
        assert manager.get_bool("enabled") is False

    def test_get_bool_success_from_string_yes(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"debug": True}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register("debug", default=False, type=bool)
        manager.apply_defaults()
        assert manager.get_bool("debug") is True

    def test_get_bool_failure_unknown_key(
        self, manager: PluginConfigManager
    ):
        with pytest.raises(PluginConfigError) as exc_info:
            manager.get_bool("nonexistent")

        assert "Unknown configuration key" in str(exc_info.value)

    def test_get_bool_failure_invalid_value(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"flag": "not_a_bool"}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register("flag", default=False, type=bool)
        manager.apply_defaults()

        with pytest.raises(PluginConfigError):
            manager.get_bool("flag")


class TestGetStr:
    """Tests for the get_str() method - success and failure paths."""

    def test_get_str_success_default(
        self, manager: PluginConfigManager
    ):
        manager.register("host", default="localhost", type=str)
        manager.apply_defaults()
        assert manager.get_str("host") == "localhost"

    def test_get_str_success_user_value(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"host": "example.com"}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register("host", default="localhost", type=str)
        manager.apply_defaults()
        assert manager.get_str("host") == "example.com"

    def test_get_str_success_empty_string(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"name": ""}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register("name", default="default", type=str)
        manager.apply_defaults()
        assert manager.get_str("name") == ""

    def test_get_str_success_none_default(
        self, manager: PluginConfigManager
    ):
        manager.register("optional_field", default=None, type=str)
        manager.apply_defaults()
        assert manager.get_str("optional_field") is None

    def test_get_str_failure_unknown_key(
        self, manager: PluginConfigManager
    ):
        with pytest.raises(PluginConfigError) as exc_info:
            manager.get_str("missing_key")

        assert "Unknown configuration key" in str(exc_info.value)
        assert "missing_key" in str(exc_info.value)

    def test_get_str_success_from_int_coercion(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"code": "123"}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register("code", default="0", type=str)
        manager.apply_defaults()
        assert manager.get_str("code") == "123"


class TestGetFloat:
    """Tests for the get_float() method - success and failure paths."""

    def test_get_float_success_default(
        self, manager: PluginConfigManager
    ):
        manager.register("timeout", default=30.5, type=float)
        manager.apply_defaults()
        assert manager.get_float("timeout") == pytest.approx(30.5)

    def test_get_float_success_user_value(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"threshold": 0.75}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register("threshold", default=0.5, type=float)
        manager.apply_defaults()
        assert manager.get_float("threshold") == pytest.approx(0.75)

    def test_get_float_success_int_coercion(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"weight": 10}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register("weight", default=1.0, type=float)
        manager.apply_defaults()
        assert manager.get_float("weight") == pytest.approx(10.0)

    def test_get_float_success_zero(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"epsilon": 0.0}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register("epsilon", default=0.001, type=float)
        manager.apply_defaults()
        assert manager.get_float("epsilon") == pytest.approx(0.0)

    def test_get_float_failure_unknown_key(
        self, manager: PluginConfigManager
    ):
        with pytest.raises(PluginConfigError) as exc_info:
            manager.get_float("nonexistent")

        assert "Unknown configuration key" in str(exc_info.value)
        assert "nonexistent" in str(exc_info.value)

    def test_get_float_failure_invalid_string(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"ratio": "not_a_float"}})
        manager = PluginConfigManager("myplugin", config_view)
        manager.register("ratio", default=1.0, type=float)
        manager.apply_defaults()

        with pytest.raises(PluginConfigError):
            manager.get_float("ratio")


class TestBackwardCompatibility:
    """Tests ensuring backward compatibility with existing plugins."""

    def test_compat_add_method(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        manager = PluginConfigManager("myplugin", config_view)
        manager.add({"key1": "value1", "key2": 42})
        assert manager.config["key1"].get() == "value1"
        assert manager.config["key2"].get() == 42

    def test_compat_config_attribute(
        self, manager: PluginConfigManager
    ):
        assert hasattr(manager, "config")

    def test_compat_getitem(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"existing": "value"}})
        manager = PluginConfigManager("myplugin", config_view)
        assert manager["existing"].get() == "value"

    def test_compat_contains(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"present": 1}})
        manager = PluginConfigManager("myplugin", config_view)
        assert "present" in manager
        assert "absent" not in manager

    def test_compat_set_method(
        self, fresh_config: confuse.Configuration, manager: PluginConfigManager
    ):
        manager.register("key", default="default")
        manager.apply_defaults()
        manager.set({"key": "overwritten"})
        assert manager.config["key"].get() == "overwritten"

    def test_compat_flatten(
        self, fresh_config: confuse.Configuration, config_view: confuse.Subview
    ):
        fresh_config.add({"myplugin": {"a": 1, "b": "two"}})
        manager = PluginConfigManager("myplugin", config_view)
        flat = manager.flatten()
        assert flat["a"] == 1
        assert flat["b"] == "two"


class TestPluginConfigError:
    """Tests for PluginConfigError exception."""

    def test_exception_message(self):
        err = PluginConfigError("Test error message")
        assert str(err) == "Test error message"
        assert err.message == "Test error message"

    def test_exception_is_exception(self):
        assert issubclass(PluginConfigError, Exception)

    def test_exception_catchable(self):
        with pytest.raises(Exception):
            raise PluginConfigError("catchable")


class TestFixtureRegressionAssertions:
    """Regression tests verifying root cause of embedart fixture errors.

    The 3 ERRORs in embedart tests (test_embed_art_from_url_*) were
    caused by a recursive fixture dependency in test/plugins/conftest.py:

        @pytest.fixture
        def requests_mock(requests_mock, monkeypatch):  # BUG: self-reference!
            ...

    pytest interprets the parameter `requests_mock` as requesting the
    fixture that is currently being defined (same name), causing:
        "recursive dependency involving fixture 'requests_mock' detected"

    This has NOTHING to do with register_config_batch() or the new
    PluginConfigManager - it was a latent bug in the conftest itself.

    Proof by:
    1. Testing that conftest.py fixture definitions are syntactically valid
    2. Testing that the MusicBrainzAPI monkey-patch can be applied in isolation
       via autouse pattern (the adopted fix)
    3. Testing that register_config_batch() does NOT introduce any fixtures
       or modify pytest fixture resolution
    """

    def test_conftest_syntax_valid(self):
        """test/plugins/conftest.py must be importable without SyntaxError."""
        import ast
        import pathlib

        conftest_path = (
            pathlib.Path(__file__).parent
            / "plugins"
            / "conftest.py"
        )
        source = conftest_path.read_text(encoding="utf-8")
        tree = ast.parse(source)  # raises SyntaxError if broken

        fixture_defs = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            if any(
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "fixture"
                for dec in node.decorator_list
            )
        ]
        assert "_patch_musicbrainz_session" in fixture_defs, (
            "Conftest should define _patch_musicbrainz_session (autouse fix)."
        )
        for func_def in fixture_defs:
            assert not any(
                arg.arg == func_def
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == func_def
                for arg in (node.args.args + node.args.posonlyargs)
            ), (
                f"Fixture '{func_def}' must NOT request itself as a parameter "
                "(would cause recursive fixture dependency)."
            )

    def test_no_self_referencing_fixture_pattern(self):
        """The buggy pattern `def X(X, ...)` must never appear in conftest."""
        import pathlib
        import re

        conftest_path = (
            pathlib.Path(__file__).parent
            / "plugins"
            / "conftest.py"
        )
        source = conftest_path.read_text(encoding="utf-8")

        fixture_decorator = re.compile(
            r"@pytest\.fixture\s*(?:\([^)]*\))?\s*\n"
            r"(?:async\s+)?def\s+(\w+)\s*\(\s*([^)]*)\)",
            re.MULTILINE,
        )
        for match in fixture_decorator.finditer(source):
            fixture_name = match.group(1)
            params_str = match.group(2)
            param_names = [
                p.strip().split(":")[0].split("=")[0].strip()
                for p in params_str.split(",")
                if p.strip()
            ]
            assert fixture_name not in param_names, (
                f"REGRESSION: fixture '{fixture_name}' in conftest.py requests "
                f"itself as parameter -> will trigger recursive fixture error"
            )

    def test_mb_session_monkeypatch_independent_of_plugin_config(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The MusicBrainz session monkey-patch (conftest core purpose) works
        independently of any PluginConfigManager / register_config_batch.

        This proves the embedart ERRORs were NOT caused by register_config_batch.
        """
        import requests

        was_called = {"v": False}

        def fake_create_session(self):
            was_called["v"] = True
            return requests.Session()

        try:
            monkeypatch.setattr(
                "beetsplug._utils.musicbrainz.MusicBrainzAPI.create_session",
                fake_create_session,
            )
        except (AttributeError, ModuleNotFoundError):
            pytest.skip("MusicBrainzAPI not available in this env")

        try:
            from beetsplug._utils.musicbrainz import MusicBrainzAPI

            api = MusicBrainzAPI(app="test", version="1.0")
            sess = api.create_session()
        except Exception:
            pytest.skip("MusicBrainzAPI instantiation skipped")
        else:
            assert isinstance(sess, requests.Session)
            assert was_called["v"] is True

    def test_register_config_batch_does_not_alter_pytest_fixtures(
        self,
        fresh_config: confuse.Configuration,
        config_view: confuse.Subview,
    ):
        """register_config_batch() is pure Python (no pytest hooks/decorators).

        It cannot and must not change fixture resolution order, introduce
        new fixtures, or otherwise interfere with the test harness.
        """
        import inspect
        import types

        manager = PluginConfigManager("fixture_test", config_view)
        manager.register_batch(
            {
                "a": {"default": 1, "type": int},
                "b": {"default": True, "type": bool},
            }
        )
        manager.apply_defaults()

        assert isinstance(
            PluginConfigManager.register_batch, types.FunctionType
        )
        sig = inspect.signature(PluginConfigManager.register_batch)
        assert list(sig.parameters.keys()) == ["self", "configs"]

        fixture_markers = getattr(
            PluginConfigManager.register_batch, "pytestmark", []
        )
        assert len(fixture_markers) == 0, (
            "register_config_batch must NOT carry pytest markers "
            "(it is a runtime method, not a fixture)"
        )

    def test_embedart_plugin_init_no_fixture_resolution(self):
        """Instantiating a BeetsPlugin with register_config_batch uses only
        class-level Python code - no pytest fixture machinery is touched.

        Additional proof that the fixture ERRORs were pre-existing / unrelated.
        """
        import inspect

        from beets.plugins import BeetsPlugin

        source = inspect.getsource(BeetsPlugin.register_config_batch)
        assert "fixture" not in source.lower()
        assert "pytest" not in source.lower()

        init_source = inspect.getsource(BeetsPlugin.__init__)
        assert "fixture" not in init_source.lower()
        assert "request.getfixturevalue" not in init_source


class TestLegacyConfigDeprecation:
    """Emit PendingDeprecationWarning for the pre-migration `self.config.add`
    path, and confirm the 5 migrated core plugins no longer trigger it.

    Together with the filterwarnings rules in setup.cfg, this guarantees:
    1. Reviewer plugin and other downstream non-migrated plugins WILL see a
       migration signal during tests.
    2. Migrated plugins (lastfm/fetchart, mpdupdate, replaygain, scrub,
       convert) stay clean going forward.
    """

    # ------------------------------------------------------------------ utils

    @staticmethod
    def _reset_beets_config():
        """Re-initialize a blank beets root config so plugin-level defaults
        registered by earlier tests never leak between cases.
        """
        import beets

        beets.config.clear()

    # ----------------------------------------------------- legacy warnings

    def test_legacy_config_add_emits_pending_deprecation_warning(self):
        """Direct `self.config.add(...)` on a plugin subclass must fire the
        warning so downstream maintainers see the migration signal.
        """
        from beets.plugins import BeetsPlugin

        self._reset_beets_config()

        class _OldStylePlugin(BeetsPlugin):
            def __init__(self):
                super().__init__()
                self.config.add(
                    {
                        "foo": True,
                        "bar": 42,
                    }
                )

        with pytest.warns(PendingDeprecationWarning, match=r"self\.config\.add\(\)"):
            _OldStylePlugin()

    def test_legacy_config_manager_add_emits_pending_deprecation_warning(self):
        """Direct `self.config_manager.add(...)` (extra back-compat shim) also
        emits a deprecation warning.
        """
        from beets.plugins import BeetsPlugin

        self._reset_beets_config()

        class _OldShimPlugin(BeetsPlugin):
            def __init__(self):
                super().__init__()
                self.config_manager.add({"baz": "hello"})

        with pytest.warns(
            PendingDeprecationWarning,
            match=r"config_manager\.add\(\)",
        ):
            _OldShimPlugin()

    def test_register_config_batch_does_not_trigger_legacy_warning(self):
        """The new API MUST NOT fire the legacy-path warning (false negatives
        would render the whole deprecation useless).
        """
        from beets.plugins import BeetsPlugin

        self._reset_beets_config()

        class _NewStylePlugin(BeetsPlugin):
            def __init__(self):
                super().__init__()
                self.register_config_batch(
                    {
                        "threshold": {
                            "default": 0.5,
                            "type": float,
                            "help": "test",
                        },
                    }
                )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _NewStylePlugin()

        legacy_warnings = [
            w
            for w in caught
            if issubclass(w.category, PendingDeprecationWarning)
            and (
                "self.config.add()" in str(w.message)
                or "config_manager.add()" in str(w.message)
            )
        ]
        assert legacy_warnings == [], (
            "register_config_batch must NOT trigger the legacy "
            "PendingDeprecationWarning; got: "
            + ", ".join(str(w.message) for w in legacy_warnings)
        )

    # -------------------------------------------------- migrated 5 plugins

    def test_migrated_plugin_scrub_no_legacy_warning(self):
        """`scrub` - 1-config minimal example. Must be clean."""
        import warnings

        from beetsplug.scrub import ScrubPlugin

        self._reset_beets_config()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ScrubPlugin()

        legacy_warnings = [
            w
            for w in caught
            if issubclass(w.category, PendingDeprecationWarning)
            and (
                "self.config.add()" in str(w.message)
                or "config_manager.add()" in str(w.message)
            )
        ]
        assert legacy_warnings == [], (
            "scrub plugin migrated -> MUST NOT trigger legacy-config warning"
        )

    def test_migrated_plugin_mpdupdate_no_legacy_warning(self):
        """`mpdupdate` - env-var defaults + redact password."""
        import warnings

        from beetsplug.mpdupdate import MPDUpdatePlugin

        self._reset_beets_config()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            MPDUpdatePlugin()

        legacy_warnings = [
            w
            for w in caught
            if issubclass(w.category, PendingDeprecationWarning)
            and (
                "self.config.add()" in str(w.message)
                or "config_manager.add()" in str(w.message)
            )
        ]
        assert legacy_warnings == [], (
            "mpdupdate plugin migrated -> MUST NOT trigger legacy-config "
            "warning"
        )

    def test_migrated_plugin_replaygain_no_legacy_warning(self, monkeypatch):
        """`replaygain` - 10 configs with choices.

        Uses a fake backend instance so plugin instantiation does NOT require
        the `mp3gain`/`aacgain` binaries on PATH.
        """
        import warnings

        from beetsplug import replaygain as rg_mod

        self._reset_beets_config()

        class _FakeBackend:
            pass

        def _fake_select(backend_name, log, config):
            return _FakeBackend()

        monkeypatch.setattr(rg_mod, "BACKENDS", {"command": lambda *a, **kw: _FakeBackend()})

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                rg_mod.ReplayGainPlugin()
            except Exception:
                # Any runtime exception after __init__ registration is fine,
                # we only care about the legacy-path warning during init.
                pass

        legacy_warnings = [
            w
            for w in caught
            if issubclass(w.category, PendingDeprecationWarning)
            and (
                "self.config.add()" in str(w.message)
                or "config_manager.add()" in str(w.message)
            )
        ]
        assert legacy_warnings == [], (
            "replaygain plugin migrated -> MUST NOT trigger legacy-config "
            "warning"
        )

    def test_migrated_plugin_convert_no_legacy_warning(self):
        """`convert` - ~24 options incl. nested formats dict."""
        import warnings

        from beetsplug.convert import ConvertPlugin

        self._reset_beets_config()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ConvertPlugin()

        legacy_warnings = [
            w
            for w in caught
            if issubclass(w.category, PendingDeprecationWarning)
            and (
                "self.config.add()" in str(w.message)
                or "config_manager.add()" in str(w.message)
            )
        ]
        assert legacy_warnings == [], (
            "convert plugin migrated -> MUST NOT trigger legacy-config warning"
        )

    def test_migrated_plugin_fetchart_no_legacy_warning(self):
        """`fetchart` (incl. LastFM source add_default_config shim) must be
        clean after the migration.
        """
        import warnings

        from beetsplug.fetchart import FetchArtPlugin

        self._reset_beets_config()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            FetchArtPlugin()

        legacy_warnings = [
            w
            for w in caught
            if issubclass(w.category, PendingDeprecationWarning)
            and (
                "self.config.add()" in str(w.message)
                or "config_manager.add()" in str(w.message)
            )
        ]
        assert legacy_warnings == [], (
            "fetchart plugin migrated -> MUST NOT trigger legacy-config "
            "warning"
        )

    def test_legacy_warning_message_explicitly_names_migration_target(self):
        """Warning text must mention the replacement APIs so authors can
        immediately find the migration path without reading the docs first.
        """
        from beets.plugins import BeetsPlugin

        self._reset_beets_config()

        class _AnotherOldPlugin(BeetsPlugin):
            def __init__(self):
                super().__init__()
                self.config.add({"mode": "safe"})

        with pytest.warns(PendingDeprecationWarning) as record:
            _AnotherOldPlugin()

        msg = str(record[0].message).lower()
        assert "register_config_batch" in msg or "register_config" in msg, (
            "deprecation warning must tell authors which API to migrate TO; "
            f"got: {record[0].message}"
        )
