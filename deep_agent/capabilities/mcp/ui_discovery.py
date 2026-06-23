from __future__ import annotations

import json
from typing import Any

from loguru import logger
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities.mcp.builtin_config import is_builtin_mcp_row
from deep_agent.capabilities.mcp.toolset_builder import build_discovery_toolset
from deep_agent.capabilities.ui.constants import (
    DISCOVERY_TIMEOUT_SEC,
    MAX_TOOLS_PER_MCP,
    UI_TOOLS_LIST_TOOL,
    UI_TOOLS_MANIFEST_URI,
)
from deep_agent.capabilities.ui.manifest import parse_manifest_payload
from deep_agent.db.models import McpServer, UiToolDefinition
from deep_agent.db.stores.ui_tool import UiToolStoreBase
from deep_agent.schemas.ui_tools import UiToolsRefreshResponse


def resolve_ui_tools_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("ui_tools") or {}
    if not isinstance(raw, dict):
        return {"enabled": True, "mode": "tool", "refresh_seconds": 300}
    return {
        "enabled": raw.get("enabled", True),
        "mode": raw.get("mode", "tool"),
        "refresh_seconds": int(raw.get("refresh_seconds", 300)),
    }


def _discovery_context() -> RunContext[AgentDeps]:
    return RunContext(
        deps=AgentDeps(session_id="__ui_discovery__"),
        model=TestModel(),
        usage=RunUsage(),
    )


def _normalize_tool_result(raw: Any) -> Any:
    if isinstance(raw, str):
        return json.loads(raw) if raw.strip() else {}
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, "model_dump"):
        return raw.model_dump()
    if isinstance(raw, list) and raw:
        first = raw[0]
        if hasattr(first, "text"):
            text = first.text
            return json.loads(text) if isinstance(text, str) and text.strip() else {}
    return json.loads(str(raw))


async def fetch_ui_tools_manifest(row: McpServer) -> list[UiToolDefinition]:
    if is_builtin_mcp_row(row.name, row.transport, row.config):
        return []

    cfg = resolve_ui_tools_config(row.config)
    if not cfg["enabled"] or cfg["mode"] == "none":
        return []

    toolset = build_discovery_toolset(row.name, row.transport, row.config)
    ctx = _discovery_context()
    active = await toolset.for_run(ctx)

    async with active:
        mode = cfg["mode"]
        if mode == "tool":
            tools = await active.get_tools(ctx)
            if UI_TOOLS_LIST_TOOL not in tools:
                logger.info("MCP server {!r} has no {}", row.name, UI_TOOLS_LIST_TOOL)
                return []
            raw = await active.call_tool(UI_TOOLS_LIST_TOOL, {}, ctx, tools[UI_TOOLS_LIST_TOOL])
            payload = _normalize_tool_result(raw)
        elif mode == "resource":
            contents = await active.read_resource(UI_TOOLS_MANIFEST_URI)
            if isinstance(contents, str):
                payload = json.loads(contents)
            elif isinstance(contents, bytes):
                payload = json.loads(contents.decode("utf-8"))
            elif isinstance(contents, dict):
                payload = contents
            else:
                payload = _normalize_tool_result(contents)
        else:
            logger.warning("Unknown ui_tools.mode {!r} for MCP {}", cfg["mode"], row.name)
            return []

    definitions = parse_manifest_payload(payload, mcp_server_name=row.name)
    if len(definitions) > MAX_TOOLS_PER_MCP:
        raise ValueError(f"MCP {row.name!r} returned more than {MAX_TOOLS_PER_MCP} UI tools")
    return definitions


async def discover_and_persist_ui_tools(
    ui_tool_store: UiToolStoreBase,
    row: McpServer,
    *,
    use_cache_on_error: bool = True,
) -> UiToolsRefreshResponse:
    if is_builtin_mcp_row(row.name, row.transport, row.config):
        return UiToolsRefreshResponse(
            mcp_server=row.name,
            discovered=0,
            upserted=0,
            removed=0,
            errors=[],
        )

    errors: list[str] = []

    try:
        import asyncio

        definitions = await asyncio.wait_for(
            fetch_ui_tools_manifest(row),
            timeout=DISCOVERY_TIMEOUT_SEC,
        )
        upserted, removed = await ui_tool_store.sync_mcp_tools(row.name, definitions)
        return UiToolsRefreshResponse(
            mcp_server=row.name,
            discovered=len(definitions),
            upserted=upserted,
            removed=removed,
            errors=errors,
        )
    except Exception as exc:
        message = str(exc)
        errors.append(message)
        logger.exception("UI tool discovery failed for MCP server {!r}", row.name)
        if use_cache_on_error:
            cached = await ui_tool_store.list_all(
                source="mcp",
                mcp_server_name=row.name,
                enabled=True,
            )
            return UiToolsRefreshResponse(
                mcp_server=row.name,
                discovered=len(cached),
                upserted=0,
                removed=0,
                errors=errors,
            )
        return UiToolsRefreshResponse(
            mcp_server=row.name,
            discovered=0,
            upserted=0,
            removed=0,
            errors=errors,
        )


def non_builtin_mcp_names_for_spec(capabilities: list[str] | None) -> set[str]:
    """MCP UI discovery runs only for explicitly enabled non-builtin MCP capabilities."""
    if not capabilities:
        return set()
    return {cap.removeprefix("mcp:") for cap in capabilities if cap.startswith("mcp:")}
