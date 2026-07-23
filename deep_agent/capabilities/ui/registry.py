from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger
from pydantic_ai import ExternalToolset
from pydantic_ai.tools import ToolDefinition

from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.ui.builtin import builtin_ui_records
from deep_agent.capabilities.ui.constants import SOURCE_PRIORITY
from deep_agent.capabilities.ui.external import RetryableExternalToolset, build_args_validator
from deep_agent.capabilities.ui.types import UIToolRecord
from deep_agent.db.stores.bundle import DataStores


@dataclass(frozen=True)
class EffectiveUiTools:
    definitions: list[UIToolRecord]
    shadowed: dict[str, str]
    has_dynamic: bool


def is_tool_visible(
    record: UIToolRecord,
    *,
    capabilities: list[str] | None,
) -> bool:
    if not record.enabled:
        return False
    if capabilities is None or f"ui:{record.name}" not in capabilities:
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
) -> dict[str, dict[str, Any]]:
    if stores.ui_tool is None:
        return {}
    effective = await load_effective_ui_tools(stores, spec)
    return build_tool_hints(effective.definitions)


async def load_effective_ui_tools(
    stores: DataStores,
    spec: AgentSpec,
) -> EffectiveUiTools:
    db_rows = await stores.ui_tool.list_all(enabled=True)
    db_records = [UIToolRecord.from_row(row) for row in db_rows if row.source == "manual"]

    visible_db = [
        record
        for record in db_records
        if is_tool_visible(
            record,
            capabilities=spec.capabilities,
        )
    ]

    merged, shadowed = merge_definitions(builtin_ui_records(), visible_db)
    has_dynamic = any(record.source == "manual" for record in merged)
    if shadowed:
        logger.warning("UI tool name conflicts resolved: {}", shadowed)
    return EffectiveUiTools(definitions=merged, shadowed=shadowed, has_dynamic=has_dynamic)


async def load_selectable_ui_tools(
    stores: DataStores,
) -> list[UIToolRecord]:
    """List enabled extension UI tools that can be assigned to an Agent."""
    db_rows = await stores.ui_tool.list_all(enabled=True)
    db_records = [UIToolRecord.from_row(row) for row in db_rows if row.source == "manual"]
    merged, _ = merge_definitions(builtin_ui_records(), db_records)
    return [record for record in merged if record.source == "manual"]


async def prepare_ui_toolset_for_spec(
    stores: DataStores,
    spec: AgentSpec,
) -> tuple[ExternalToolset | None, dict[str, dict[str, Any]]]:
    effective = await load_effective_ui_tools(stores, spec)
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
