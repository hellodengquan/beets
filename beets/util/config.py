from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, TypeVar

import confuse

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence


def sanitize_choices(
    choices: Sequence[str], choices_all: Collection[str]
) -> list[str]:
    """Clean up a stringlist configuration attribute: keep only choices
    elements present in choices_all, remove duplicate elements, expand '*'
    wildcard while keeping original stringlist order.
    """
    seen: set[str] = set()
    others = [x for x in choices_all if x not in choices]
    res: list[str] = []
    for s in choices:
        if s not in seen:
            if s in list(choices_all):
                res.append(s)
            elif s == "*":
                res.extend(others)
        seen.add(s)
    return res


def sanitize_pairs(
    pairs: Sequence[tuple[str, str]],
    pairs_all: Sequence[tuple[str, str]],
    raise_on_unknown: bool = False,
) -> list[tuple[str, str]]:
    """Clean up a single-element mapping configuration attribute as returned
    by Confuse's `Pairs` template: keep only two-element tuples present in
    pairs_all, remove duplicate elements, expand ('str', '*') and ('*', '*')
    wildcards while keeping the original order. Note that ('*', '*') and
    ('*', 'whatever') have the same effect.
    Set raise_on_unknown to raise an error when a provided pair is not recognised

    For example,

    >>> sanitize_pairs(
    ...     [('foo', 'baz bar'), ('key', '*'), ('*', '*')],
    ...     [('foo', 'bar'), ('foo', 'baz'), ('foo', 'foobar'),
    ...      ('key', 'value')]
    ...     )
    [('foo', 'baz'), ('foo', 'bar'), ('key', 'value'), ('foo', 'foobar')]
    """
    pairs_all = list(pairs_all)
    seen: set[tuple[str, str]] = set()
    others = [x for x in pairs_all if x not in pairs]
    res: list[tuple[str, str]] = []
    for k, values in pairs:
        for v in values.split():
            x = (k, v)
            if x in pairs_all:
                if x not in seen:
                    seen.add(x)
                    res.append(x)
            elif k == "*":
                new = [o for o in others if o not in seen]
                seen.update(new)
                res.extend(new)
            elif v == "*":
                new = [o for o in others if o not in seen and o[0] == k]

                if len(new) == 0 and raise_on_unknown:
                    raise UnknownPairError(k, v)

                seen.update(new)
                res.extend(new)
            elif raise_on_unknown:
                raise UnknownPairError(k, v)
    return res


class UnknownPairError(Exception):
    def __init__(self, k, v):
        super().__init__(f"setting {k}={v} is not recognized")


T = TypeVar("T")


@dataclass
class PluginConfigSchema:
    """插件配置项的元数据定义。"""

    key: str
    default: Any = None
    type: type | confuse.Template | None = None
    choices: list[Any] | None = None
    help: str = ""
    required: bool = False
    validator: Callable[[Any], bool] | None = None
    deprecated: bool = False
    deprecation_message: str = ""
    redact: bool = False


@dataclass
class PluginConfigManager:
    """集中式插件配置管理器。

    提供统一的配置注册、默认值设置、类型验证和错误处理机制。
    """

    plugin_name: str
    config_view: confuse.Subview
    _schemas: dict[str, PluginConfigSchema] = field(default_factory=dict)
    _registered_defaults: dict[str, Any] = field(default_factory=dict)
    _validated: bool = False

    def __post_init__(self) -> None:
        """初始化后设置向后兼容的别名。"""
        self.config = self.config_view
        self._log = None

    def register(
        self,
        key: str,
        default: Any = None,
        type: type | confuse.Template | None = None,
        choices: list[Any] | None = None,
        help: str = "",
        required: bool = False,
        validator: Callable[[Any], bool] | None = None,
        deprecated: bool = False,
        deprecation_message: str = "",
        redact: bool = False,
    ) -> None:
        """注册一个配置项及其元数据。

        Args:
            key: 配置项的键名
            default: 默认值
            type: 配置项的类型或 confuse 模板
            choices: 可选值列表
            help: 配置项的帮助说明
            required: 是否为必填项
            validator: 自定义验证函数
            deprecated: 是否已废弃
            deprecation_message: 废弃警告信息
            redact: 是否在日志中隐藏（敏感信息）
        """
        schema = PluginConfigSchema(
            key=key,
            default=default,
            type=type,
            choices=choices,
            help=help,
            required=required,
            validator=validator,
            deprecated=deprecated,
            deprecation_message=deprecation_message,
            redact=redact,
        )
        self._schemas[key] = schema
        self._registered_defaults[key] = default

    def register_batch(self, configs: dict[str, dict[str, Any]]) -> None:
        """批量注册多个配置项。

        Args:
            configs: 字典，键为配置项名，值为该配置项的参数字典
        """
        for key, params in configs.items():
            self.register(key=key, **params)

    def apply_defaults(self) -> None:
        """将所有已注册的默认值应用到配置视图中。"""
        if self._registered_defaults:
            self.config_view.add(self._registered_defaults)
        self._apply_redactions()

    def _apply_redactions(self) -> None:
        """为敏感配置项应用脱敏标记。"""
        for key, schema in self._schemas.items():
            if schema.redact:
                try:
                    self.config_view[key].redact = True
                except Exception:
                    pass

    def validate(self) -> None:
        """验证所有已注册的配置项。

        Raises:
            PluginConfigError: 当配置验证失败时
        """
        errors: list[str] = []

        for key, schema in self._schemas.items():
            try:
                self._validate_single(key, schema)
            except PluginConfigError as e:
                errors.append(str(e))

        if errors:
            raise PluginConfigError(
                f"Plugin '{self.plugin_name}' configuration errors:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )

        self._validated = True

    def _validate_single(self, key: str, schema: PluginConfigSchema) -> None:
        """验证单个配置项。"""
        config_item = self.config_view[key]

        if schema.deprecated and self._is_user_configured(key):
            import warnings

            msg = (
                schema.deprecation_message
                or f"Configuration option '{key}' is deprecated"
            )
            warnings.warn(f"{self.plugin_name}: {msg}", DeprecationWarning)

        try:
            value = config_item.get()
        except confuse.NotFoundError:
            if schema.required:
                raise PluginConfigError(
                    f"'{key}' is required but not configured"
                )
            return
        except (confuse.ConfigTypeError, confuse.ConfigValueError) as e:
            raise PluginConfigError(f"'{key}': {e}")

        if schema.required and value is None:
            raise PluginConfigError(
                f"'{key}' is required but not configured"
            )

        if schema.choices is not None and value not in schema.choices:
            raise PluginConfigError(
                f"'{key}' must be one of {schema.choices}, got {value!r}"
            )

        if schema.validator is not None:
            try:
                if not schema.validator(value):
                    raise PluginConfigError(
                        f"'{key}' failed custom validation: {value!r}"
                    )
            except PluginConfigError:
                raise
            except Exception as e:
                raise PluginConfigError(
                    f"'{key}' validation error: {e}"
                )

    def _is_user_configured(self, key: str) -> bool:
        """检查配置项是否由用户显式设置（而非使用默认值）。"""
        try:
            for source in self.config_view.root().sources:
                plugin_config = source.get(self.plugin_name)
                if plugin_config and key in plugin_config:
                    return True
        except Exception:
            pass
        return False

    def get(self, key: str, type: type | None = None) -> Any:
        """安全地获取配置值。

        Args:
            key: 配置项键名
            type: 期望的类型（覆盖注册时的类型）

        Returns:
            配置项的值

        Raises:
            PluginConfigError: 当配置项不存在或类型不匹配时
        """
        if key not in self._schemas:
            raise PluginConfigError(
                f"Unknown configuration key: '{key}'. "
                f"Available keys: {list(self._schemas.keys())}"
            )

        schema = self._schemas[key]
        config_item = self.config_view[key]
        resolved_type = type or schema.type

        try:
            try:
                raw_value = config_item.get()
            except confuse.NotFoundError:
                if schema.required:
                    raise PluginConfigError(
                        f"'{key}' is required but not configured"
                    )
                return schema.default

            if raw_value is None and schema.default is None:
                return None

            if resolved_type is not None:
                return config_item.get(resolved_type)
            return raw_value
        except confuse.NotFoundError:
            if schema.required:
                raise PluginConfigError(
                    f"'{key}' is required but not configured"
                )
            return schema.default
        except (confuse.ConfigTypeError, confuse.ConfigValueError) as e:
            raise PluginConfigError(f"'{key}': {e}")

    def get_int(self, key: str) -> int:
        """获取整数类型的配置值。"""
        return self.get(key, type=int)

    def get_bool(self, key: str) -> bool:
        """获取布尔类型的配置值。"""
        return self.get(key, type=bool)

    def get_str(self, key: str) -> str:
        """获取字符串类型的配置值。"""
        return self.get(key, type=str)

    def get_float(self, key: str) -> float:
        """获取浮点数类型的配置值。"""
        return self.get(key, type=float)

    def as_str_seq(self, key: str) -> list[str]:
        """获取字符串序列类型的配置值。"""
        return self.config_view[key].as_str_seq()

    def as_choice(
        self, key: str, choices: dict[str, Any] | list[str]
    ) -> Any:
        """从选项中获取配置值。"""
        return self.config_view[key].as_choice(choices)

    def has(self, key: str) -> bool:
        """检查配置项是否存在。"""
        return key in self.config_view

    def __contains__(self, key: str) -> bool:
        return key in self.config_view

    def __getitem__(self, key: str) -> confuse.Subview:
        """直接获取 confuse 子视图（向后兼容）。"""
        return self.config_view[key]

    def set_args(self, opts: Any) -> None:
        """从命令行选项设置配置值。"""
        self.config_view.set_args(opts)

    def set(self, data: dict[str, Any]) -> None:
        """设置配置值（覆盖现有值）。"""
        self.config_view.set(data)

    def add(self, defaults: dict[str, Any]) -> None:
        """添加默认配置（向后兼容）。

        .. deprecated:: 2.0
            Use ``register_config_batch`` or ``register_config`` instead
            so each option gets proper type metadata and validation.
        """
        import warnings

        warnings.warn(
            f"Plugin '{self.plugin_name}' is using the deprecated "
            f"`self.config_manager.add()` API. Please migrate to "
            f"`self.register_config_batch()` to register configuration "
            f"options with explicit metadata (default, type, help, ...).",
            PendingDeprecationWarning,
            stacklevel=2,
        )
        self.config_view.add(defaults)
        for key, value in defaults.items():
            if key not in self._registered_defaults:
                self._registered_defaults[key] = value

    def flatten(self) -> dict[str, Any]:
        """将配置展平为字典。"""
        return self.config_view.flatten()


class PluginConfigError(Exception):
    """插件配置错误的统一异常类型。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
