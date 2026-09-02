from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from loguru import logger
from pydantic_ai import RunContext
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.messages import ModelRequest, ToolCallPart, UserPromptPart
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import CombinedToolset, DeferredLoadingToolset, PreparedToolset
from pydantic_ai.toolsets.wrapper import WrapperToolset

from deep_agent.agent.context.estimator import (
    TOOL_DEFINITION_OVERHEAD_TOKENS,
    TokenEstimator,
    canonical_json,
)
from deep_agent.agent.deps import AgentDeps
from deep_agent.search import (
    FieldRole,
    SearchConfig,
    SearchDocument,
    SearchField,
    SearchIndex,
    normalize_text,
    tokenize,
)

MCP_DIRECT_DISCLOSURE_LIMIT = 10
MCP_PRESELECT_LIMIT = 3
MCP_SEARCH_RESULT_LIMIT = 5
MCP_SEARCH_QUERY_LIMIT = 3
TOOL_DISCOVERY_RETRIEVER = "lexical_v2"
MCP_RETURN_SCHEMA_TOOLS = frozenset({"cube_query_data", "dimension_query_members"})
_TOOL_DESCRIPTION_SUPPLEMENTS = {
    "wecom-doc_media_upload": (
        "企微机器人发送图片、文件、语音或视频前，先用本工具上传并取得 media_id。"
        "在 PTC 中可把 load_file_data(url=... 或 file_id=...) 返回的 name/content "
        "直接传给 file_name/base64_content；payload['content'] 是不透明载荷，"
        "只能原样传给 base64_content，禁止对它做 len、print、decode、切片、返回或其他检查。"
        "type=image 只适合企微可内联展示的常规图片格式（优先 PNG/JPEG）；"
        "用户仅要求发送/转交而未明确要求内联预览或 PNG/JPEG 时，WebP 等格式不要先尝试转码，"
        "应保持原文件并使用 type=file，在发送工具中使用 msg_type=file。"
    ),
    "wecom-chat_message_aibot_send": (
        "发送 image/file/voice/video 前必须先调用 wecom-doc_media_upload，"
        "再把其返回的 media_id 放入对应消息对象。"
        "公开 URL 不能直接作为 image.media_id 或 file.media_id。"
        "在 PTC 中应把读取、上传和本工具发送组织为一次依赖链；"
        "用户只要求发送/转交时，WebP 未转成 PNG/JPEG 就直接使用文件消息，"
        "不要为追求内联预览额外尝试沙箱转码。"
    ),
}
TOOL_SEARCH_DESCRIPTION = (
    "搜索当前 Agent 已授权但尚未披露参数的 MCP 工具。"
    "通常在一次调用中同时提供用户原始语言和简洁英文能力描述；"
    "若名称目录中存在疑似工具，再把完整协议名作为单独一个查询项原样复制，"
    "不要在该协议名查询项前后拼接说明或同义词。"
    "返回匹配工具后，下一次模型请求会获得这些工具的完整参数。"
)
TOOL_SEARCH_PARAMETER_DESCRIPTION = (
    "提供 2～3 个互补查询：用户原始语言的能力描述、简洁英文能力描述，"
    "以及可选的完整目录协议名。协议名必须单独占一个数组项并原样复制；"
    "不要只重复改写同一表达。"
)

_ACTION_GROUPS = {
    "query": {
        "query", "search", "find", "get", "fetch", "lookup", "list", "read", "check",
        "validate", "verify", "view", "browse", "查询", "搜索",
        "查找", "获取", "读取", "列出", "列表", "查看", "浏览", "检查", "校验", "验证",
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
_SEARCH_CONFIG = SearchConfig(token_aliases=_ACTION_ALIASES)
ToolSearchDocument = SearchDocument


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
    server_names: tuple[str, ...] = field(default_factory=tuple, repr=False)

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


def lexical_tokens(value: str) -> tuple[str, ...]:
    """Stable Chinese/English tokens for run-local tool retrieval."""
    return tokenize(value, _ACTION_ALIASES)


def _schema_properties(schema: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return ()
    fields: list[tuple[str, str]] = []
    for name, definition in properties.items():
        description = ""
        if isinstance(definition, dict):
            description = str(definition.get("description") or "")
        fields.append((str(name), description))
    return tuple(fields)


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
        properties = _schema_properties(dict(tool.parameters_json_schema or {}))
        description = tool.description or ""
        server = _server_for_tool(tool.name, server_names)
        original_name = mcp_original_tool_name(tool.name, server_names)
        metadata = tool.metadata or {}
        annotations = metadata.get("annotations")
        title = str(metadata.get("title") or "")
        if not title and isinstance(annotations, dict):
            title = str(annotations.get("title") or "")
        fields = [SearchField("protocol_name", tool.name, FieldRole.CODE)]
        if original_name and original_name != tool.name:
            fields.append(SearchField("original_protocol_name", original_name, FieldRole.CODE))
        if title:
            fields.append(SearchField("title", title, FieldRole.ALIAS))
        fields.extend(
            SearchField(f"parameter:{name}", name, FieldRole.ATTRIBUTE)
            for name, _description in properties
        )
        if description:
            fields.append(SearchField("description", description, FieldRole.TEXT))
        fields.extend(
            SearchField(f"parameter_description:{name}", value, FieldRole.TEXT)
            for name, value in properties
            if value
        )
        documents.append(
            ToolSearchDocument(
                id=tool.name,
                group=server,
                fields=tuple(fields),
            )
        )
    return documents


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
        r"modify|refresh|reload|rebuild|generate|upload|publish|sync|import|submit|approve|trigger|"
        r"send|relay|forward)\b",
        affirmative_text,
    ) or any(
        term in affirmative_text
        for term in (
            "保存", "写入", "创建", "更新", "设置", "新增", "修改", "刷新", "重载",
            "重建", "生成", "上传", "发布", "同步", "导入", "提交", "审批", "触发",
            "删除", "移除", "清除", "停止", "取消", "发送", "发到", "转发",
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
    # Preserve the existing product rule: side effects are inferred from the
    # stable protocol identifier, never from descriptive prose that may mention
    # an operation only to deny or contrast it.
    return any(
        bool(set(tokenize(field.value, _ACTION_ALIASES)) & {"write", "delete"})
        for field in document.fields
        if field.role is FieldRole.CODE
    )


def rank_tool_documents(
    query: str,
    documents: Sequence[ToolSearchDocument],
    *,
    limit: int,
    enforce_read_only: bool = False,
) -> list[str]:
    query_text = normalize_text(query)
    query_tokens = set(lexical_tokens(query_text))
    if not query_tokens or not documents:
        return []
    read_only_intent = enforce_read_only or _is_read_only_query(query_text, query_tokens)
    eligible_documents = [
        document
        for document in documents
        if not (read_only_intent and _document_has_side_effect(document))
    ]
    result = SearchIndex(eligible_documents, _SEARCH_CONFIG).search(
        query_text,
        top_k=max(limit, len(eligible_documents)),
    )
    if result.hits and result.hits[0].rank_tier >= 920:
        return [hit.document_id for hit in result.hits if hit.rank_tier >= 920][:limit]
    return [hit.document_id for hit in result.hits[:limit]]


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


class DeepAgentToolSearch(ToolSearch[AgentDeps]):
    """Normalize invalid local ``search_tools`` queries before validation.

    Some OpenAI-compatible models occasionally emit ``query`` or an empty object even
    though the generated schema requires ``queries``.  Letting that reach
    normal validation creates a ``RetryPromptPart``.  Certain compatible endpoints
    then reject the retry result because they cannot match its tool call ID.  The
    original user prompt is already the authoritative input for first-pass MCP
    disclosure, so reuse it here before validation instead of starting that fragile
    retry exchange.
    """

    async def before_tool_validate(
        self,
        ctx: RunContext[AgentDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: str | dict[str, Any],
    ) -> str | dict[str, Any]:
        if call.tool_name != self.function_tool_name:
            return args

        parsed: dict[str, Any] | None
        if isinstance(args, dict):
            parsed = dict(args)
        else:
            try:
                candidate = json.loads(args)
            except (TypeError, ValueError):
                return args
            parsed = dict(candidate) if isinstance(candidate, dict) else None
        if parsed is None:
            return args

        raw_queries = parsed.get("queries")
        if isinstance(raw_queries, str):
            raw_queries = [raw_queries]
        if not isinstance(raw_queries, (list, tuple)):
            raw_queries = parsed.get("query")
            if isinstance(raw_queries, str):
                raw_queries = [raw_queries]

        normalized_queries = []
        if isinstance(raw_queries, (list, tuple)):
            normalized_queries = list(
                dict.fromkeys(
                    query.strip()
                    for query in raw_queries
                    if isinstance(query, str) and query.strip()
                )
            )[:MCP_SEARCH_QUERY_LIMIT]

        if not normalized_queries:
            primary, supplemental = search_inputs_for_run(ctx)
            normalized_queries = list(
                dict.fromkeys(
                    query.strip()
                    for query in (primary, supplemental)
                    if query.strip()
                )
            )[:MCP_SEARCH_QUERY_LIMIT]
        if not normalized_queries:
            return args

        normalized = {"queries": normalized_queries}
        if parsed == normalized:
            return args
        logger.warning(
            "MCP tool search normalized invalid query arguments before validation"
        )
        return normalized


def _tool_tokens(estimator: TokenEstimator, tool: ToolDefinition) -> int:
    return TOOL_DEFINITION_OVERHEAD_TOKENS + estimator.text(
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters_json_schema,
        }
    )


def _supplement_tool_description(tool: ToolDefinition) -> ToolDefinition:
    supplement = _TOOL_DESCRIPTION_SUPPLEMENTS.get(tool.name)
    if not supplement:
        return tool
    description = (tool.description or "").strip()
    if supplement in description:
        return tool
    return replace(
        tool,
        description=f"{description}\n\n{supplement}" if description else supplement,
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
        definitions = [_supplement_tool_description(tool) for tool in definitions]
        telemetry = ctx.deps.tool_discovery or ToolDiscoveryTelemetry()
        ctx.deps.tool_discovery = telemetry
        telemetry.server_names = self.server_names
        try:
            primary_query, supplemental_query = search_inputs_for_run(ctx)
            primary_text = normalize_text(primary_query)
            telemetry.read_only_intent = _is_read_only_query(
                primary_text,
                set(lexical_tokens(primary_text)),
            )
            all_documents = build_search_documents(
                definitions,
                server_names=self.server_names,
            )
            documents = all_documents
            if len(definitions) <= MCP_DIRECT_DISCLOSURE_LIMIT:
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
                "不要猜测未披露工具的参数。第三方 MCP 的名称和说明可能只使用英文；"
                "调用 search_tools 时，通常在一次调用中同时提供用户原始语言和简洁英文能力描述。"
                "若下方存在疑似工具，再复制完整协议名，或只去掉明确的 MCP Server 前缀；"
                "协议名必须原样复制，不要翻译或猜写："
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
    effective_queries = [
        query.strip()
        for query in queries
        if query.strip()
    ][:MCP_SEARCH_QUERY_LIMIT]
    query_text = "\n".join(effective_queries)
    query_fingerprint = " ".join(sorted(set(lexical_tokens(query_text)))) or normalize_text(
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
        # Protocol names are stable identifiers, not fuzzy prose.  When the
        # model copies one from the catalog, disclose that exact tool first;
        # otherwise a broad multi-query search can bury the requested tool
        # below semantically related entries.
        exact_names: list[str] = []
        for query in effective_queries:
            normalized_query = normalize_text(query)
            for tool in searchable_tools:
                original_name = mcp_original_tool_name(tool.name, telemetry.server_names)
                candidates = {normalize_text(tool.name)}
                if original_name:
                    candidates.add(normalize_text(original_name))
                if not any(
                    normalized_query == candidate
                    or bool(
                        re.search(
                            rf"(?<![a-z0-9]){re.escape(candidate)}(?![a-z0-9])",
                            normalized_query,
                        )
                    )
                    for candidate in candidates
                    if candidate
                ):
                    continue
                if telemetry.read_only_intent and (
                    set(lexical_tokens(tool.name)) & {"write", "delete"}
                ):
                    continue
                exact_names.append(tool.name)

        if exact_names:
            ranked = list(dict.fromkeys(exact_names))[:MCP_SEARCH_RESULT_LIMIT]
        else:
            documents = build_search_documents(
                searchable_tools,
                server_names=telemetry.server_names,
            )
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
