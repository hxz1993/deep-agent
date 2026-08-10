from __future__ import annotations

import hashlib
import math
import re
import time
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from loguru import logger
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import CombinedToolset, DeferredLoadingToolset, PreparedToolset
from pydantic_ai.toolsets.wrapper import WrapperToolset

from deep_agent.agent.context.estimator import (
    TOOL_DEFINITION_OVERHEAD_TOKENS,
    TokenEstimator,
    canonical_json,
)
from deep_agent.agent.deps import AgentDeps

MCP_PRESELECT_LIMIT = 3
MCP_SEARCH_RESULT_LIMIT = 5
TOOL_DISCOVERY_RETRIEVER = "lexical_v1"
MCP_RETURN_SCHEMA_TOOLS = frozenset({"cube_query_data", "dimension_query_members"})

_ASCII_PART_RE = re.compile(r"[a-z0-9]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_ACTION_GROUPS = {
    "query": {
        "query", "search", "find", "get", "fetch", "lookup", "list", "read", "check",
        "validate", "verify", "查询", "搜索", "查找", "获取", "读取", "列表",
        "检查", "校验", "验证",
    },
    "write": {
        "write", "save", "create", "update", "set", "insert", "run", "execute",
        "start", "stop", "cancel", "modify", "refresh", "reload", "rebuild",
        "generate", "upload", "publish", "sync", "import", "submit", "approve",
        "trigger", "保存", "写入", "创建", "更新", "设置", "新增", "修改", "刷新",
        "重载", "重建", "生成", "上传", "发布", "同步", "导入", "提交", "审批",
        "触发", "运行", "执行", "启动", "停止", "取消",
    },
    "delete": {"delete", "remove", "drop", "clear", "删除", "移除", "清除"},
}
_ACTION_ALIASES = {
    alias: canonical
    for canonical, aliases in _ACTION_GROUPS.items()
    for alias in aliases
}


@dataclass(frozen=True)
class ToolSearchDocument:
    name: str
    server: str
    description: str
    parameter_names: tuple[str, ...]
    parameter_descriptions: tuple[str, ...]
    name_tokens: tuple[str, ...]
    parameter_tokens: tuple[str, ...]
    description_tokens: tuple[str, ...]


@dataclass
class ToolDiscoveryTelemetry:
    candidate_count: int = 0
    direct_count: int = 0
    deferred_count: int = 0
    preselected_tools: list[str] = field(default_factory=list)
    search_calls: int = 0
    discovered_tools: list[str] = field(default_factory=list)
    estimated_full_schema_tokens: int = 0
    estimated_disclosed_schema_tokens: int = 0
    estimated_saved_schema_tokens: int = 0
    retriever: str = TOOL_DISCOVERY_RETRIEVER
    catalog_hash: str | None = None
    retrieval_ms: float = 0.0
    fallback_reason: str | None = None
    return_schema_tools: list[str] = field(default_factory=list)
    return_schema_unavailable: list[str] = field(default_factory=list)
    estimated_return_schema_tokens: int = 0
    read_only_intent: bool = field(default=False, repr=False)
    candidate_tools: list[str] = field(default_factory=list, repr=False)
    seen_search_queries: set[str] = field(default_factory=set, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_count": self.candidate_count,
            "direct_count": self.direct_count,
            "deferred_count": self.deferred_count,
            "preselected_tools": list(self.preselected_tools),
            "search_calls": self.search_calls,
            "discovered_tools": list(self.discovered_tools),
            "estimated_full_schema_tokens": self.estimated_full_schema_tokens,
            "estimated_disclosed_schema_tokens": self.estimated_disclosed_schema_tokens,
            "estimated_saved_schema_tokens": self.estimated_saved_schema_tokens,
            "retriever": self.retriever,
            "return_schema_tools": list(self.return_schema_tools),
            "return_schema_unavailable": list(self.return_schema_unavailable),
            "estimated_return_schema_tokens": self.estimated_return_schema_tokens,
        }


def _normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = _CAMEL_BOUNDARY_RE.sub(" ", value)
    return re.sub(r"\s+", " ", value.replace("_", " ").replace("-", " ")).strip().lower()


def lexical_tokens(value: str) -> tuple[str, ...]:
    """Stable Chinese/English tokens for run-local tool retrieval."""
    normalized = _normalize_text(value)
    tokens: list[str] = []
    for token in _ASCII_PART_RE.findall(normalized):
        tokens.append(_ACTION_ALIASES.get(token, token))
    for segment in _CJK_RE.findall(normalized):
        chars = list(segment)
        if len(chars) == 1:
            tokens.append(_ACTION_ALIASES.get(chars[0], chars[0]))
        tokens.extend(
            _ACTION_ALIASES.get(chars[index] + chars[index + 1], chars[index] + chars[index + 1])
            for index in range(len(chars) - 1)
        )
        for alias, canonical in _ACTION_ALIASES.items():
            if any(ord(char) > 127 for char in alias) and alias in segment:
                tokens.append(canonical)
    return tuple(token for token in tokens if token)


def _schema_properties(schema: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return (), ()
    names: list[str] = []
    descriptions: list[str] = []
    for name, definition in properties.items():
        names.append(str(name))
        if isinstance(definition, dict) and definition.get("description"):
            descriptions.append(str(definition["description"]))
    return tuple(names), tuple(descriptions)


def _server_for_tool(name: str, server_names: Sequence[str]) -> str:
    matches = [server for server in server_names if name.startswith(f"{server}_")]
    return max(matches, key=len) if matches else "mcp"


def mcp_original_tool_name(name: str, server_names: Sequence[str]) -> str | None:
    """Recover an MCP tool's original protocol name from its runtime prefix."""

    matches = [server for server in server_names if name.startswith(f"{server}_")]
    if not matches:
        return None
    server = max(matches, key=len)
    return name[len(server) + 1 :]


def build_return_schema_selector(
    *,
    server_names: Sequence[str],
):
    """Select the small evidence-backed MCP output-schema allowlist."""

    async def selector(ctx: RunContext[AgentDeps], tool: ToolDefinition) -> bool:
        original_name = mcp_original_tool_name(tool.name, server_names)
        return original_name in MCP_RETURN_SCHEMA_TOOLS and bool(tool.return_schema)

    return selector


def build_search_documents(
    tools: Sequence[ToolDefinition],
    *,
    server_names: Sequence[str] = (),
) -> list[ToolSearchDocument]:
    documents: list[ToolSearchDocument] = []
    for tool in tools:
        parameter_names, parameter_descriptions = _schema_properties(
            dict(tool.parameters_json_schema or {})
        )
        description = tool.description or ""
        server = _server_for_tool(tool.name, server_names)
        documents.append(
            ToolSearchDocument(
                name=tool.name,
                server=server,
                description=description,
                parameter_names=parameter_names,
                parameter_descriptions=parameter_descriptions,
                name_tokens=lexical_tokens(f"{server} {tool.name}"),
                parameter_tokens=lexical_tokens(" ".join(parameter_names)),
                description_tokens=lexical_tokens(
                    " ".join((description, *parameter_descriptions))
                ),
            )
        )
    return documents


def _idf(documents: Sequence[ToolSearchDocument], query_tokens: set[str]) -> dict[str, float]:
    count = len(documents)
    result: dict[str, float] = {}
    for token in query_tokens:
        frequency = sum(
            token in set((*doc.name_tokens, *doc.parameter_tokens, *doc.description_tokens))
            for doc in documents
        )
        result[token] = math.log(1 + (count - frequency + 0.5) / (frequency + 0.5))
    return result


def _bm25_field(tokens: tuple[str, ...], query_tokens: set[str], idf: dict[str, float]) -> float:
    if not tokens:
        return 0.0
    frequencies = Counter(tokens)
    length = max(1, len(tokens))
    score = 0.0
    for token in query_tokens:
        tf = frequencies.get(token, 0)
        if not tf:
            continue
        score += idf[token] * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * length / 12))
    return score


def _is_read_only_query(query_text: str, query_tokens: set[str]) -> bool:
    explicit_read_only = "只读" in query_text or bool(
        re.search(r"\b(?:read\s*only|readonly)\b", query_text)
    )
    explicit_negative_side_effect = bool(
        re.search(
            r"(?:不要|不需要|无需|禁止|不得|不可)[^，。；\n]*(?:保存|写入|创建|更新|"
            r"设置|新增|修改|刷新|重载|重建|生成|上传|发布|同步|导入|提交|审批|"
            r"触发|运行|执行|启动|停止|取消|删除|移除|清除)",
            query_text,
        )
        or re.search(
            r"\b(?:do\s+not|don't|without|never)\b[^,.;\n]*(?:write|save|create|"
            r"update|set|insert|delete|remove|drop|clear|stop|cancel|modify|refresh|"
            r"reload|rebuild|generate|upload|publish|sync|import|submit|approve|trigger|"
            r"run|execute|start)",
            query_text,
        )
    )
    if "query" not in query_tokens and not explicit_read_only and not explicit_negative_side_effect:
        return False
    affirmative_text = re.sub(
        r"(?:不要|不需要|无需|禁止|不得|不可)[^，。；\n]*",
        " ",
        query_text,
    )
    affirmative_text = re.sub(
        r"\b(?:do\s+not|don't|without|never)\b[^,.;\n]*",
        " ",
        affirmative_text,
    )
    if re.search(
        r"\b(?:write|save|create|update|set|insert|delete|remove|drop|clear|stop|cancel|"
        r"modify|refresh|reload|rebuild|generate|upload|publish|sync|import|submit|approve|trigger)\b",
        affirmative_text,
    ) or any(
        term in affirmative_text
        for term in (
            "保存", "写入", "创建", "更新", "设置", "新增", "修改", "刷新", "重载",
            "重建", "生成", "上传", "发布", "同步", "导入", "提交", "审批", "触发",
            "删除", "移除", "清除", "停止", "取消",
        )
    ):
        return False
    run_terms = {"run", "execute", "start", "运行", "执行", "启动"}
    readback_terms = {"status", "result", "log", "detail", "状态", "结果", "日志", "详情"}
    if any(term in affirmative_text for term in run_terms) and not any(
        term in affirmative_text for term in readback_terms
    ):
        return False
    return True


def _document_has_side_effect(document: ToolSearchDocument) -> bool:
    return bool(set(document.name_tokens) & {"write", "delete"})


def rank_tool_documents(
    query: str,
    documents: Sequence[ToolSearchDocument],
    *,
    limit: int,
    enforce_read_only: bool = False,
) -> list[str]:
    query_text = _normalize_text(query)
    query_tokens = set(lexical_tokens(query_text))
    if not query_tokens or not documents:
        return []
    idf = _idf(documents, query_tokens)
    read_only_intent = enforce_read_only or _is_read_only_query(query_text, query_tokens)
    subject_tokens = query_tokens - {"query", "write", "delete"}
    scored: list[tuple[float, str]] = []
    for document in documents:
        all_document_tokens = set(
            (*document.name_tokens, *document.parameter_tokens, *document.description_tokens)
        )
        if read_only_intent and _document_has_side_effect(document):
            continue
        normalized_name = _normalize_text(document.name)
        name_match = bool(
            query_text
            and (query_text == normalized_name or query_text in normalized_name or normalized_name in query_text)
        )
        if subject_tokens and not (subject_tokens & all_document_tokens) and not name_match:
            continue
        score = (
            4 * _bm25_field(document.name_tokens, query_tokens, idf)
            + 2 * _bm25_field(document.parameter_tokens, query_tokens, idf)
            + _bm25_field(document.description_tokens, query_tokens, idf)
        )
        if query_text and query_text == normalized_name:
            score += 20
        elif query_text and (query_text in normalized_name or normalized_name in query_text):
            score += 8
        if score > 0:
            scored.append((score, document.name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [name for _score, name in scored[:limit]]


def _context_ref_search_text(value: dict[str, Any]) -> str:
    if value.get("kind") != "context-ref":
        return ""
    return " ".join(
        str(value.get(key) or "") for key in ("type", "label")
    ).strip()


def _visible_prompt_text(value: Any) -> str:
    primary, supplemental = _visible_prompt_search_text(value)
    return "\n".join(filter(None, (primary, supplemental)))


def _visible_prompt_search_text(value: Any) -> tuple[str, str]:
    if isinstance(value, str):
        return value, ""
    if isinstance(value, dict):
        if value.get("kind") == "text-content":
            return str(value.get("content") or ""), ""
        return "", _context_ref_search_text(value)
    if isinstance(value, (list, tuple)):
        primary: list[str] = []
        supplemental: list[str] = []
        for item in value:
            item_primary, item_supplemental = _visible_prompt_search_text(item)
            if item_primary:
                primary.append(item_primary)
            if item_supplemental:
                supplemental.append(item_supplemental)
        return "\n".join(primary), "\n".join(supplemental)
    return "", ""


def search_inputs_for_run(ctx: RunContext[AgentDeps]) -> tuple[str, str]:
    if ctx.deps.tool_discovery_inputs_explicit:
        return (
            ctx.deps.tool_discovery_primary_query,
            ctx.deps.tool_discovery_supplemental_query,
        )
    primary, supplemental = _visible_prompt_search_text(ctx.prompt)
    if primary.strip() or supplemental.strip():
        return primary, supplemental
    for message in reversed(ctx.messages):
        if not isinstance(message, ModelRequest):
            continue
        for part in reversed(message.parts):
            if isinstance(part, UserPromptPart):
                primary, supplemental = _visible_prompt_search_text(part.content)
                if primary.strip() or supplemental.strip():
                    return primary, supplemental
    return "", ""


def search_text_for_run(ctx: RunContext[AgentDeps]) -> str:
    primary, supplemental = search_inputs_for_run(ctx)
    return "\n".join(filter(None, (primary, supplemental)))


def _tool_tokens(estimator: TokenEstimator, tool: ToolDefinition) -> int:
    return TOOL_DEFINITION_OVERHEAD_TOKENS + estimator.text(
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters_json_schema,
        }
    )


def _return_schema_telemetry(
    tools: Sequence[ToolDefinition],
    *,
    disclosed_names: set[str],
    server_names: Sequence[str],
    estimator: TokenEstimator,
) -> tuple[list[str], list[str], int]:
    selected: list[str] = []
    unavailable: list[str] = []
    tokens = 0
    for tool in tools:
        if tool.name not in disclosed_names:
            continue
        if mcp_original_tool_name(tool.name, server_names) not in MCP_RETURN_SCHEMA_TOOLS:
            continue
        if not tool.return_schema:
            unavailable.append(tool.name)
            continue
        selected.append(tool.name)
        tokens += estimator.text({"Return schema": tool.return_schema})
    return selected, unavailable, tokens


def _catalog_hash(tools: Sequence[ToolDefinition]) -> str:
    payload = canonical_json(
        [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters_json_schema,
            }
            for tool in sorted(tools, key=lambda item: item.name)
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class McpDiscoveryToolset(WrapperToolset[AgentDeps]):
    """Run-scoped MCP disclosure policy around Pydantic AI's native wrappers."""

    server_names: tuple[str, ...]
    estimator: TokenEstimator

    async def prepare_tools(
        self,
        ctx: RunContext[AgentDeps],
        definitions: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        started = time.perf_counter()
        telemetry = ctx.deps.tool_discovery or ToolDiscoveryTelemetry()
        ctx.deps.tool_discovery = telemetry
        try:
            primary_query, supplemental_query = search_inputs_for_run(ctx)
            primary_text = _normalize_text(primary_query)
            telemetry.read_only_intent = _is_read_only_query(
                primary_text,
                set(lexical_tokens(primary_text)),
            )
            all_documents = build_search_documents(
                definitions,
                server_names=self.server_names,
            )
            documents = all_documents
            if len(definitions) <= MCP_PRESELECT_LIMIT:
                selected = [tool.name for tool in definitions]
            else:
                selected = rank_tool_documents(
                    primary_query,
                    documents,
                    limit=MCP_PRESELECT_LIMIT,
                    enforce_read_only=telemetry.read_only_intent,
                )
                if (
                    not selected
                    and not primary_query.strip()
                    and supplemental_query.strip()
                ):
                    supplemental_selected = rank_tool_documents(
                        supplemental_query,
                        documents,
                        limit=MCP_PRESELECT_LIMIT,
                        enforce_read_only=telemetry.read_only_intent,
                    )
                    selected = list(
                        dict.fromkeys([*selected, *supplemental_selected])
                    )[:MCP_PRESELECT_LIMIT]
            selected_set = set(selected)
            prepared = [
                replace(tool, defer_loading=False)
                if tool.name in selected_set
                else tool
                for tool in definitions
            ]
            full_tokens = sum(_tool_tokens(self.estimator, tool) for tool in definitions)
            full_tokens += _return_schema_telemetry(
                definitions,
                disclosed_names={tool.name for tool in definitions},
                server_names=self.server_names,
                estimator=self.estimator,
            )[2]
            candidate_names = {tool.name for tool in definitions}
            discovered_names = set(ctx.discovered_tool_names) & candidate_names
            disclosed_names = selected_set | discovered_names
            direct_tokens = sum(
                _tool_tokens(self.estimator, tool)
                for tool in prepared
                if not tool.defer_loading or tool.name in disclosed_names
            )
            (
                telemetry.return_schema_tools,
                telemetry.return_schema_unavailable,
                telemetry.estimated_return_schema_tokens,
            ) = _return_schema_telemetry(
                prepared,
                disclosed_names=disclosed_names,
                server_names=self.server_names,
                estimator=self.estimator,
            )
            direct_tokens += telemetry.estimated_return_schema_tokens
            telemetry.candidate_count = len(definitions)
            telemetry.candidate_tools = [tool.name for tool in definitions]
            telemetry.direct_count = len(disclosed_names)
            telemetry.deferred_count = len(definitions) - len(disclosed_names)
            telemetry.preselected_tools = selected
            telemetry.estimated_full_schema_tokens = full_tokens
            telemetry.estimated_disclosed_schema_tokens = direct_tokens
            telemetry.estimated_saved_schema_tokens = max(0, full_tokens - direct_tokens)
            telemetry.catalog_hash = _catalog_hash(definitions)
            if telemetry.search_calls == 0 and telemetry.retrieval_ms == 0:
                logger.info(
                    "MCP tool disclosure catalog={} candidates={} direct={} deferred={} "
                    "preselected={} estimated_saved_tokens={}",
                    telemetry.catalog_hash,
                    telemetry.candidate_count,
                    telemetry.direct_count,
                    telemetry.deferred_count,
                    telemetry.preselected_tools,
                    telemetry.estimated_saved_schema_tokens,
                )
            return prepared
        except Exception as exc:
            telemetry.fallback_reason = type(exc).__name__
            fallback_definitions = definitions
            if telemetry.read_only_intent:
                fallback_definitions = [
                    tool
                    for tool in definitions
                    if not (set(lexical_tokens(tool.name)) & {"write", "delete"})
                ]
            telemetry.candidate_count = len(fallback_definitions)
            telemetry.candidate_tools = [tool.name for tool in fallback_definitions]
            telemetry.direct_count = len(fallback_definitions)
            telemetry.preselected_tools = [tool.name for tool in fallback_definitions]
            telemetry.estimated_full_schema_tokens = sum(
                _tool_tokens(self.estimator, tool) for tool in fallback_definitions
            )
            telemetry.estimated_disclosed_schema_tokens = telemetry.estimated_full_schema_tokens
            logger.exception(
                "MCP tool preselection failed; disclosing the authorized catalog "
                "within the run read-only policy"
            )
            return [replace(tool, defer_loading=False) for tool in fallback_definitions]
        finally:
            telemetry.retrieval_ms += (time.perf_counter() - started) * 1_000

    async def get_instructions(self, ctx: RunContext[AgentDeps]) -> list[str] | None:
        original = await self.wrapped.get_instructions(ctx)
        parts: list[str] = []
        if isinstance(original, str):
            parts.append(original)
        elif original:
            parts.extend(str(part.content if hasattr(part, "content") else part) for part in original)

        telemetry = ctx.deps.tool_discovery
        if telemetry is None or telemetry.deferred_count <= 0:
            return parts or None
        deferred_names = sorted(
            set(telemetry.candidate_tools)
            - set(telemetry.preselected_tools)
            - set(ctx.discovered_tool_names)
            - {"search_tools"}
        )
        if deferred_names:
            grouped: dict[str, list[str]] = defaultdict(list)
            for name in deferred_names:
                grouped[_server_for_tool(name, self.server_names)].append(name)
            rows = [
                "以下 MCP 工具当前仅提供名称；需要其完整参数时调用 search_tools，"
                "不要猜测未披露工具的参数："
            ]
            for server in sorted(grouped):
                rows.append(f"- {server}: {', '.join(sorted(grouped[server]))}")
            parts.append("\n".join(rows))
        return parts or None


def build_mcp_discovery_toolset(
    toolsets: Sequence[Any],
    *,
    server_names: Sequence[str],
    estimator: TokenEstimator,
) -> McpDiscoveryToolset | None:
    if not toolsets:
        return None
    combined = CombinedToolset(list(toolsets))
    deferred = DeferredLoadingToolset(combined)
    wrapper = McpDiscoveryToolset(
        wrapped=deferred,
        server_names=tuple(server_names),
        estimator=estimator,
    )
    wrapper.wrapped = PreparedToolset(deferred, wrapper.prepare_tools)
    return wrapper


def search_deferred_tools(
    ctx: RunContext[AgentDeps],
    queries: Sequence[str],
    tools: Sequence[ToolDefinition],
) -> list[str]:
    """Pydantic AI ToolSearch callback using the same generic run-local catalog."""
    telemetry = ctx.deps.tool_discovery or ToolDiscoveryTelemetry()
    ctx.deps.tool_discovery = telemetry
    started = time.perf_counter()
    telemetry.search_calls += 1
    query_text = "\n".join(query for query in queries if query.strip())
    query_fingerprint = " ".join(sorted(set(lexical_tokens(query_text)))) or _normalize_text(
        query_text
    )
    if query_fingerprint in telemetry.seen_search_queries:
        logger.info(
            "MCP tool search catalog={} call={} skipped_duplicate_query=true",
            telemetry.catalog_hash,
            telemetry.search_calls,
        )
        return []
    telemetry.seen_search_queries.add(query_fingerprint)
    excluded_names = (
        set(ctx.discovered_tool_names)
        | set(telemetry.discovered_tools)
        | set(telemetry.preselected_tools)
    )
    searchable_tools = [tool for tool in tools if tool.name not in excluded_names]
    try:
        documents = build_search_documents(searchable_tools)
        ranked = rank_tool_documents(
            query_text,
            documents,
            limit=MCP_SEARCH_RESULT_LIMIT,
            enforce_read_only=telemetry.read_only_intent,
        )
    except Exception as exc:  # reveal the authorized corpus instead of dropping access
        telemetry.fallback_reason = type(exc).__name__
        logger.exception("MCP tool search failed; returning the authorized deferred catalog")
        fallback_tools = searchable_tools
        if telemetry.read_only_intent:
            fallback_tools = [
                tool
                for tool in searchable_tools
                if not (
                    set(lexical_tokens(tool.name)) & {"write", "delete"}
                )
            ]
        ranked = sorted(tool.name for tool in fallback_tools)[:MCP_SEARCH_RESULT_LIMIT]
    finally:
        telemetry.retrieval_ms += (time.perf_counter() - started) * 1_000
    telemetry.discovered_tools = list(
        dict.fromkeys([*telemetry.discovered_tools, *ranked])
    )
    telemetry.direct_count = len(
        set(telemetry.preselected_tools) | set(telemetry.discovered_tools)
    )
    telemetry.deferred_count = max(0, telemetry.candidate_count - telemetry.direct_count)
    logger.info(
        "MCP tool search catalog={} call={} matches={} retrieval_ms={:.2f}",
        telemetry.catalog_hash,
        telemetry.search_calls,
        ranked,
        (time.perf_counter() - started) * 1_000,
    )
    return ranked
