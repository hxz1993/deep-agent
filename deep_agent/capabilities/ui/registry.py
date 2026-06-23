from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger
from pydantic_ai import ExternalToolset
from pydantic_ai.tools import ToolDefinition
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.ui.builtin import builtin_ui_records
from deep_agent.capabilities.ui.constants import SOURCE_PRIORITY
from deep_agent.capabilities.mcp.builtin_config import is_builtin_mcp_row
from deep_agent.capabilities.mcp.ui_discovery import (
    discover_and_persist_ui_tools,
    non_builtin_mcp_names_for_spec,
    resolve_ui_tools_config,
)
from deep_agent.db.deps import get_mcp_repo, get_ui_tool_repo
from deep_agent.capabilities.ui.types import UIToolRecord


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
    return ExternalToolset(tools, id=toolset_id)


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
        if payload:
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


async def ensure_mcp_ui_tools_discovered(db: AsyncSession, spec: AgentSpec) -> None:
    """Discover MCP UI tools on demand when building an agent with non-builtin MCP."""
    mcp_names = non_builtin_mcp_names_for_spec(spec.capabilities)
    if not mcp_names:
        return

    mcp_repo = get_mcp_repo()
    for name in sorted(mcp_names):
        row = await mcp_repo.get_by_name(db, name)
        if row is None or not row.enabled or is_builtin_mcp_row(row.name, row.transport, row.config):
            continue
        cfg = resolve_ui_tools_config(row.config)
        if not cfg["enabled"] or cfg["mode"] == "none":
            continue
        await discover_and_persist_ui_tools(db, row, use_cache_on_error=True)


async def load_effective_ui_tools(
    db: AsyncSession,
    spec: AgentSpec,
    *,
    mcp_enabled_names: set[str] | None = None,
) -> EffectiveUiTools:
    repo = get_ui_tool_repo()
    db_rows = await repo.list_all(db, enabled=True)
    db_records = [UIToolRecord.from_row(row) for row in db_rows if row.source != "builtin"]

    if mcp_enabled_names is None:
        mcp_rows = await get_mcp_repo().list_all(db)
        mcp_enabled_names = {row.name for row in mcp_rows if row.enabled}

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
    db: AsyncSession,
    spec: AgentSpec,
) -> tuple[ExternalToolset | None, dict[str, dict[str, Any]]]:
    await ensure_mcp_ui_tools_discovered(db, spec)
    effective = await load_effective_ui_tools(db, spec)
    hints = build_tool_hints(effective.definitions)
    if not effective.has_dynamic:
        return None, hints
    return build_external_toolset(effective.definitions), hints
