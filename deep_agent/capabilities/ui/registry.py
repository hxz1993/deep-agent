from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger
from pydantic_ai import ExternalToolset
from pydantic_ai.tools import ToolDefinition

from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.mcp.resolver import (
    effective_mcp_names_for_ui,
    effective_mcp_rows_for_discovery,
)
from deep_agent.capabilities.mcp.db_loader import capabilities_from_rows
from deep_agent.capabilities.mcp.ui_discovery import (
    discover_and_persist_ui_tools,
    mcp_rows_for_ui_discovery,
    should_refresh_mcp_ui_tools,
)
from deep_agent.capabilities.ui.builtin import builtin_ui_records
from deep_agent.capabilities.ui.constants import SOURCE_PRIORITY
from deep_agent.capabilities.ui.external import RetryableExternalToolset, build_args_validator
from deep_agent.capabilities.ui.types import UIToolRecord
from deep_agent.db.models import McpServer
from deep_agent.db.stores.bundle import DataStores


@dataclass(frozen=True)
class EffectiveUiTools:
    definitions: list[UIToolRecord]
    shadowed: dict[str, str]
    has_dynamic: bool


def is_tool_visible(
    record: UIToolRecord,
    *,
    agent_id: str | None,
    capabilities: list[str] | None,
    mcp_enabled_names: set[str],
) -> bool:
    if not record.enabled:
        return False
    if record.agent_ids:
        if agent_id is None or agent_id not in record.agent_ids:
            return False
    if record.mcp_server_name:
        cap = f"mcp:{record.mcp_server_name}"
        if capabilities is not None:
            if cap not in capabilities and "ui" not in capabilities:
                return False
        elif record.mcp_server_name not in mcp_enabled_names:
            return False
    return True


def merge_definitions(
    *groups: list[UIToolRecord],
) -> tuple[list[UIToolRecord], dict[str, str]]:
    """Merge by canonical name; lower SOURCE_PRIORITY wins."""
    winners: dict[str, UIToolRecord] = {}
    shadowed: dict[str, str] = {}

    for group in groups:
        for record in group:
            current = winners.get(record.name)
            if current is None:
                winners[record.name] = record
                continue
            if SOURCE_PRIORITY[record.source] < SOURCE_PRIORITY[current.source]:
                shadowed[record.name] = current.source
                winners[record.name] = record
            elif SOURCE_PRIORITY[record.source] == SOURCE_PRIORITY[current.source]:
                shadowed[record.name] = current.source
                winners[record.name] = record
            else:
                shadowed[record.name] = record.source

    ordered = sorted(winners.values(), key=lambda item: item.name)
    return ordered, shadowed


def build_external_toolset(definitions: list[UIToolRecord], *, toolset_id: str = "ui-dynamic") -> ExternalToolset:
    tools = [
        ToolDefinition(
            name=item.name,
            description=item.description,
            parameters_json_schema=item.parameters_json_schema,
        )
        for item in definitions
    ]
    validators = {item.name: build_args_validator(item) for item in definitions}
    return RetryableExternalToolset(tools, validators=validators, id=toolset_id)


def build_tool_hints(definitions: list[UIToolRecord]) -> dict[str, dict[str, Any]]:
    hints: dict[str, dict[str, Any]] = {}
    for item in definitions:
        payload: dict[str, Any] = {}
        if item.widget:
            payload["widget"] = item.widget
        if item.metadata:
            payload["metadata"] = item.metadata
        if item.source != "builtin":
            payload["source"] = item.source
        if item.mcp_server_name:
            payload["mcp_server_name"] = item.mcp_server_name
        hints[item.name] = payload
    return hints


def enrich_tool_event(event: dict[str, Any], hints: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if event.get("type") != "tool_call":
        return event
    tool = event.get("tool")
    if not isinstance(tool, str):
        return event
    extra = hints.get(tool)
    if not extra:
        for record in builtin_ui_records():
            if record.name == tool:
                extra = build_tool_hints([record]).get(tool)
                break
    if not extra:
        return event
    merged = dict(event)
    merged.update(extra)
    return merged


def enrich_deferred(deferred: dict[str, Any], hints: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not hints:
        return deferred
    enriched = dict(deferred)
    for key in ("calls", "approvals"):
        parts = enriched.get(key)
        if not isinstance(parts, list):
            continue
        enriched[key] = [
            {**part, **hints.get(part.get("tool", ""), {})} if isinstance(part, dict) else part
            for part in parts
        ]
    return enriched


async def load_ui_tool_hints(
    stores: DataStores,
    spec: AgentSpec,
    *,
    mcp_rows: list[McpServer] | None = None,
) -> dict[str, dict[str, Any]]:
    if stores.ui_tool is None:
        return {}
    effective = await load_effective_ui_tools(stores, spec, mcp_rows=mcp_rows)
    return build_tool_hints(effective.definitions)


async def ensure_mcp_ui_tools_discovered(
    stores: DataStores,
    spec: AgentSpec,
    *,
    mcp_rows: list[McpServer] | None = None,
    request_headers: dict[str, str] | None = None,
) -> None:
    """Discover MCP UI tools on demand for non-builtin MCP enabled on this agent."""
    if mcp_rows is None:
        mcp_rows = await stores.mcp.list_all()

    scoped = effective_mcp_rows_for_discovery(spec, mcp_rows)
    for row in sorted(mcp_rows_for_ui_discovery(scoped), key=lambda item: item.name):
        if not await should_refresh_mcp_ui_tools(stores.ui_tool, row):
            continue
        await discover_and_persist_ui_tools(
            stores.ui_tool,
            row,
            use_cache_on_error=True,
            request_headers=request_headers,
        )


async def load_effective_ui_tools(
    stores: DataStores,
    spec: AgentSpec,
    *,
    mcp_enabled_names: set[str] | None = None,
    mcp_rows: list[McpServer] | None = None,
) -> EffectiveUiTools:
    if mcp_enabled_names is None:
        if mcp_rows is None:
            mcp_rows = await stores.mcp.list_all()
        caps = capabilities_from_rows(mcp_rows)
        mcp_enabled_names = effective_mcp_names_for_ui(spec, caps, mcp_rows)

    db_rows = await stores.ui_tool.list_all(
        enabled=True,
        agent_id=spec.agent_id,
        mcp_server_names=mcp_enabled_names,
    )
    db_records = [UIToolRecord.from_row(row) for row in db_rows if row.source != "builtin"]

    visible_db = [
        record
        for record in db_records
        if is_tool_visible(
            record,
            agent_id=spec.agent_id,
            capabilities=spec.capabilities,
            mcp_enabled_names=mcp_enabled_names,
        )
    ]

    merged, shadowed = merge_definitions(builtin_ui_records(), visible_db)
    has_dynamic = any(record.source in {"mcp", "manual"} for record in merged)
    if shadowed:
        logger.warning("UI tool name conflicts resolved: {}", shadowed)
    return EffectiveUiTools(definitions=merged, shadowed=shadowed, has_dynamic=has_dynamic)


async def prepare_ui_toolset_for_spec(
    stores: DataStores,
    spec: AgentSpec,
    *,
    mcp_rows: list[McpServer] | None = None,
    request_headers: dict[str, str] | None = None,
) -> tuple[ExternalToolset | None, dict[str, dict[str, Any]]]:
    await ensure_mcp_ui_tools_discovered(
        stores,
        spec,
        mcp_rows=mcp_rows,
        request_headers=request_headers,
    )
    effective = await load_effective_ui_tools(stores, spec, mcp_rows=mcp_rows)
    valid_definitions: list[UIToolRecord] = []
    for definition in effective.definitions:
        try:
            build_args_validator(definition)
        except ValueError as exc:
            logger.error("Skipping invalid UI tool schema '{}': {}", definition.name, exc)
            continue
        valid_definitions.append(definition)
    hints = build_tool_hints(valid_definitions)
    if not valid_definitions:
        return None, hints
    return build_external_toolset(valid_definitions), hints
