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
        "start", "stop", "cancel", "保存", "写入", "创建", "更新", "设置", "新增",
        "运行", "执行", "启动", "停止", "取消",
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
    candidate_tools: list[str] = field(default_factory=list, repr=False)

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
    if "query" not in query_tokens:
        return False
    if re.search(
        r"\b(?:write|save|create|update|set|insert|delete|remove|drop|clear|stop|cancel)\b",
        query_text,
    ) or any(
        term in query_text
        for term in ("保存", "写入", "创建", "更新", "设置", "新增", "删除", "移除", "清除", "停止", "取消")
    ):
        return False
    run_terms = {"run", "execute", "start", "运行", "执行", "启动"}
    readback_terms = {"status", "result", "log", "detail", "状态", "结果", "日志", "详情"}
    if any(term in query_text for term in run_terms) and not any(
        term in query_text for term in readback_terms
    ):
        return False
    return True


def rank_tool_documents(
    query: str,
    documents: Sequence[ToolSearchDocument],
    *,
    limit: int,
) -> list[str]:
    query_text = _normalize_text(query)
    query_tokens = set(lexical_tokens(query_text))
    if not query_tokens or not documents:
        return []
    idf = _idf(documents, query_tokens)
    read_only_intent = _is_read_only_query(query_text, query_tokens)
    subject_tokens = query_tokens - {"query", "write", "delete"}
    scored: list[tuple[float, str]] = []
    for document in documents:
        all_document_tokens = set(
            (*document.name_tokens, *document.parameter_tokens, *document.description_tokens)
        )
        document_actions = set(document.name_tokens) & {"query", "write", "delete"}
        if read_only_intent and document_actions & {"write", "delete"}:
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
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if value.get("kind") == "text-content":
            return str(value.get("content") or "")
        return _context_ref_search_text(value)
    if isinstance(value, (list, tuple)):
        return "\n".join(filter(None, (_visible_prompt_text(item) for item in value)))
    return ""


def search_text_for_run(ctx: RunContext[AgentDeps]) -> str:
    prompt = _visible_prompt_text(ctx.prompt)
    if prompt.strip():
        return prompt
    for message in reversed(ctx.messages):
        if not isinstance(message, ModelRequest):
            continue
        for part in reversed(message.parts):
            if isinstance(part, UserPromptPart):
                prompt = _visible_prompt_text(part.content)
                if prompt.strip():
                    return prompt
    return ""


def _tool_tokens(estimator: TokenEstimator, tool: ToolDefinition) -> int:
    return TOOL_DEFINITION_OVERHEAD_TOKENS + estimator.text(tool)


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
            documents = build_search_documents(
                definitions,
                server_names=self.server_names,
            )
            query = search_text_for_run(ctx)
            if len(definitions) <= MCP_PRESELECT_LIMIT:
                selected = [tool.name for tool in definitions]
            else:
                selected = rank_tool_documents(
                    query,
                    documents,
                    limit=MCP_PRESELECT_LIMIT,
                )
            selected_set = set(selected)
            prepared = [
                replace(tool, defer_loading=False)
                if tool.name in selected_set
                else tool
                for tool in definitions
            ]
            full_tokens = sum(_tool_tokens(self.estimator, tool) for tool in definitions)
            candidate_names = {tool.name for tool in definitions}
            discovered_names = set(ctx.discovered_tool_names) & candidate_names
            disclosed_names = selected_set | discovered_names
            direct_tokens = sum(
                _tool_tokens(self.estimator, tool)
                for tool in prepared
                if not tool.defer_loading or tool.name in disclosed_names
            )
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
        except Exception as exc:  # fail open: preserve the previous complete MCP surface
            telemetry.fallback_reason = type(exc).__name__
            telemetry.candidate_count = len(definitions)
            telemetry.candidate_tools = [tool.name for tool in definitions]
            telemetry.direct_count = len(definitions)
            telemetry.preselected_tools = [tool.name for tool in definitions]
            telemetry.estimated_full_schema_tokens = sum(
                _tool_tokens(self.estimator, tool) for tool in definitions
            )
            telemetry.estimated_disclosed_schema_tokens = telemetry.estimated_full_schema_tokens
            logger.exception("MCP tool preselection failed; disclosing the authorized catalog")
            return [replace(tool, defer_loading=False) for tool in definitions]
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
    try:
        documents = build_search_documents(tools)
        ranked = rank_tool_documents(
            "\n".join(query for query in queries if query.strip()),
            documents,
            limit=MCP_SEARCH_RESULT_LIMIT,
        )
    except Exception as exc:  # reveal the authorized corpus instead of dropping access
        telemetry.fallback_reason = type(exc).__name__
        logger.exception("MCP tool search failed; returning the authorized deferred catalog")
        ranked = sorted(tool.name for tool in tools)[:MCP_SEARCH_RESULT_LIMIT]
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
