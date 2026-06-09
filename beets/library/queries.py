from __future__ import annotations

import shlex
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import beets
from beets import dbcore, logging, plugins

if TYPE_CHECKING:
    from beets.dbcore import query as query_module
    from beets.dbcore.sort import Sort
    from beets.library.models import LibModel

log = logging.getLogger("beets")


# ---------------------------------------------------------------------------
# 1. 查询配置：集中管理所有查询构建的配置项，避免重复定义和判断
# ---------------------------------------------------------------------------


def get_query_prefixes() -> dict[str, query_module.FieldQueryType]:
    """获取统一的查询前缀映射。

    所有查询构建入口都应通过此函数获取前缀，确保:
    - ``:`` 正则查询、``=~`` 字符串匹配、``=`` 精确匹配在各处一致生效
    - 插件自定义的查询前缀 (``plugins.queries()``) 被统一纳入
    """
    prefixes: dict[str, query_module.FieldQueryType] = {
        ":": dbcore.query.RegexpQuery,
        "=~": dbcore.query.StringQuery,
        "=": dbcore.query.MatchQuery,
    }
    prefixes.update(plugins.queries())
    return prefixes


def get_sort_case_insensitive() -> bool:
    """获取排序是否大小写不敏感的统一配置。"""
    return beets.config["sort_case_insensitive"].get(bool)


# ---------------------------------------------------------------------------
# 2. 查询部件预处理：路径识别等规范化步骤
# ---------------------------------------------------------------------------


def normalize_query_parts(parts: Sequence[str]) -> list[str]:
    """对查询部件列表执行统一的规范化预处理。

    当前包含:
    - 隐式路径查询识别: 将存在的文件路径自动转换为 ``path:<path>``

    未来可在此扩展其他全局规范化规则。
    """
    return [
        f"path:{s}" if dbcore.query.PathQuery.is_path_query(s) else s
        for s in parts
    ]


# ---------------------------------------------------------------------------
# 3. 规范化上下文: 将配置 + 预处理封装为可复用的构建上下文
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueryNormalizationContext:
    """查询构建的统一上下文。

    集中封装查询构建所需的全部配置，避免每处调用都重复:
    - 获取前缀
    - 读取 sort_case_insensitive
    - 执行 normalize_query_parts

    典型用法::

        ctx = QueryNormalizationContext()
        query, sort = ctx.parse_sorted(parts, model_cls)
        query = ctx.build_collection(AndQuery, parts, model_cls)
    """

    prefixes: dict[str, query_module.FieldQueryType] = field(
        default_factory=get_query_prefixes
    )
    case_insensitive: bool = field(default_factory=get_sort_case_insensitive)
    apply_parts_normalization: bool = True

    # ---- 底层部件操作 -------------------------------------------------

    def normalize(self, parts: Sequence[str]) -> list[str]:
        if self.apply_parts_normalization:
            return normalize_query_parts(parts)
        return list(parts)

    # ---- 统一的查询 + 排序解析 (替代 dbcore.parse_sorted_query) -------

    def parse_sorted(
        self, parts: Sequence[str], model_cls: type[LibModel]
    ) -> tuple[query_module.Query, Sort]:
        """解析部件列表为 (Query, Sort) 元组。

        等价于 ``dbcore.parse_sorted_query``，但统一应用:
        - 规范化上下文 (prefixes, case_insensitive)
        - 部件预处理 (normalize_query_parts)
        - 错误包装 (InvalidQueryArgumentValueError -> InvalidQueryError)
        """
        normalized_parts = self.normalize(parts)
        try:
            query, sort = dbcore.parse_sorted_query(
                model_cls,
                normalized_parts,
                self.prefixes,
                self.case_insensitive,
            )
        except dbcore.query.InvalidQueryArgumentValueError as exc:
            raise dbcore.InvalidQueryError(normalized_parts, str(exc)) from exc
        log.debug("Parsed query: {!r}", query)
        log.debug("Parsed sort: {!r}", sort)
        return query, sort

    # ---- 集合查询构建 (替代 dbcore.query_from_strings) ----------------

    def build_collection(
        self,
        query_cls: type[query_module.CollectionQuery],
        parts: Sequence[str],
        model_cls: type[LibModel],
    ) -> query_module.Query:
        """将部件列表构建为指定类型的集合查询 (AndQuery / OrQuery)。

        与 ``dbcore.query_from_strings`` 等价，但使用规范化上下文。
        供插件或内部逻辑需要显式构造集合查询时使用，
        如 ``advancedrewrite`` 中的匹配规则。
        """
        normalized_parts = self.normalize(parts)
        try:
            query = dbcore.query_from_strings(
                query_cls,
                model_cls,
                self.prefixes,
                normalized_parts,
            )
        except dbcore.query.InvalidQueryArgumentValueError as exc:
            raise dbcore.InvalidQueryError(normalized_parts, str(exc)) from exc
        log.debug("Built {} query: {!r}", query_cls.__name__, query)
        return query

    # ---- 字符串查询解析 (shlex.split -> 解析) -------------------------

    def parse_string(
        self, query_string: str, model_cls: type[LibModel]
    ) -> tuple[query_module.Query, Sort]:
        """将查询字符串用 ``shlex.split`` 拆分后解析为 (Query, Sort)。"""
        if not isinstance(query_string, str):
            raise TypeError(
                f"Query must be a unicode string, got {type(query_string).__name__}: {query_string!r}"
            )
        try:
            parts = shlex.split(query_string)
        except ValueError as exc:
            raise dbcore.InvalidQueryError(query_string, str(exc)) from exc
        return self.parse_sorted(parts, model_cls)

    # ---- 统一入口: 兼容多种输入类型 -----------------------------------

    def parse(
        self,
        query: str | Sequence[str] | query_module.Query,
        model_cls: type[LibModel],
    ) -> tuple[query_module.Query, Sort | None]:
        """统一的查询解析入口, 兼容多种输入形式。

        Parameters
        ----------
        query:
            - ``str``: 查询字符串 (经 shlex.split 拆分)
            - ``Sequence[str]``: 已拆分的部件列表
            - ``Query``: 已是 Query 对象, 直接返回 (sort 为 None)
        model_cls:
            目标模型类 (Item / Album)

        Returns
        -------
        (query, sort):
            - 传入 str / list 时返回完整的 (Query, Sort)
            - 传入 Query 对象时返回 (query, None)
        """
        if isinstance(query, dbcore.Query):
            return query, None
        if isinstance(query, str):
            return self.parse_string(query, model_cls)
        if isinstance(query, (list, tuple)):
            return self.parse_sorted(list(query), model_cls)
        raise TypeError(
            f"Unsupported query type: {type(query).__name__}. "
            "Expected str, list[str], tuple[str, ...] or Query instance."
        )


# ---------------------------------------------------------------------------
# 4. 便捷的顶层函数: 保持向后兼容, 供外部直接调用
# ---------------------------------------------------------------------------


def build_query_context(
    *,
    extra_prefixes: dict[str, query_module.FieldQueryType] | None = None,
    case_insensitive: bool | None = None,
    apply_normalization: bool = True,
) -> QueryNormalizationContext:
    """创建并返回一个 :class:`QueryNormalizationContext` 实例。

    允许调用者按需覆盖部分默认配置, 同时仍然获得统一的规范化流程。
    """
    prefixes = get_query_prefixes()
    if extra_prefixes:
        prefixes.update(extra_prefixes)
    if case_insensitive is None:
        case_insensitive = get_sort_case_insensitive()
    return QueryNormalizationContext(
        prefixes=prefixes,
        case_insensitive=case_insensitive,
        apply_parts_normalization=apply_normalization,
    )


def parse_query_parts(
    parts: Sequence[str], model_cls: type[LibModel]
) -> tuple[query_module.Query, Sort]:
    """给定部件列表, 返回对应的 (Query, Sort)。

    与 :func:`dbcore.parse_sorted_query` 类似, 但额外:
    - 启用 beets 内置查询前缀 (``:``, ``=~``, ``=``) 和插件前缀
    - 将隐式路径查询转换为显式的 ``path:<query>``

    保持与旧接口完全兼容。
    """
    ctx = QueryNormalizationContext()
    return ctx.parse_sorted(parts, model_cls)


def parse_query_string(
    s: str, model_cls: type[LibModel]
) -> tuple[query_module.Query, Sort]:
    """给定查询字符串, 返回对应的 (Query, Sort)。

    字符串使用 shell 风格语法拆分为部件。

    保持与旧接口完全兼容。
    """
    message = f"Query is not unicode: {s!r}"
    assert isinstance(s, str), message
    ctx = QueryNormalizationContext()
    return ctx.parse_string(s, model_cls)
