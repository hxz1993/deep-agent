from __future__ import annotations

from typing import Any, Iterable

from loguru import logger

from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.codes import canonical_tool_package_codes
from deep_agent.capabilities.registry import ToolPackage, ToolPackageRegistry
from deep_agent.capabilities.system_tools import list_system_tool_domains
from deep_agent.schemas.mcp import McpInspection


def _display_name(value: Any, fallback: str) -> str:
    normalized = str(value).strip() if value is not None else ""
    return normalized or fallback


def _add_packages(
    mapping: dict[str, str],
    packages: Iterable[ToolPackage],
) -> None:
    for package in packages:
        for tool in package.tools:
            mapping[tool.name] = _display_name(tool.display_name, tool.name)


def build_tool_display_name_map(
    registry: ToolPackageRegistry,
    *,
    capabilities: list[str] | None,
    mcp_rows: Iterable[Any] = (),
    ui_packages: Iterable[ToolPackage] = (),
) -> dict[str, str]:
    """Build the presentation-only name map for one current Agent definition."""

    selected_codes = (
        registry.codes()
        if capabilities is None
        else canonical_tool_package_codes(capabilities)
    )
    selected_set = set(selected_codes)
    mapping: dict[str, str] = {}
    _add_packages(mapping, registry.selected(selected_codes))

    for domain in list_system_tool_domains():
        for tool in domain.tools:
            mapping[tool.name] = _display_name(tool.display_name, tool.name)

    _add_packages(mapping, ui_packages)

    for row in mcp_rows:
        server_name = str(getattr(row, "name", "") or "").strip()
        if not server_name:
            continue
        package_code = f"mcp:{server_name}"
        if capabilities is None or package_code not in selected_set:
            continue
        inspection = McpInspection.from_value(
            getattr(row, "inspection", None),
            transport=getattr(row, "transport", None),
        )
        snapshot = inspection.tools.snapshot
        if snapshot is None:
            continue
        for definition in snapshot.definitions:
            tool_name = f"{server_name}_{definition.name}"
            mapping[tool_name] = _display_name(definition.title, tool_name)

    return mapping


async def resolve_tool_display_name_map(
    registry: ToolPackageRegistry,
    stores: Any,
    *,
    capabilities: list[str] | None,
) -> dict[str, str]:
    """Resolve current mutable UI/MCP definitions without affecting history storage."""

    mcp_rows: list[Any] = []
    ui_packages: list[ToolPackage] = []
    if capabilities is not None:
        try:
            mcp_rows = list(await stores.mcp.list_all())
        except Exception as exc:  # presentation enrichment must fail open
            logger.warning(
                "Failed to resolve MCP display names from current config: {}",
                type(exc).__name__,
            )
        try:
            from deep_agent.capabilities.ui.registry import (
                prepare_ui_tool_packages_for_spec,
            )

            spec = AgentSpec(
                name="history-display-names",
                model="",
                instructions="",
                capabilities=canonical_tool_package_codes(capabilities),
            )
            ui_packages, _hints = await prepare_ui_tool_packages_for_spec(stores, spec)
        except Exception as exc:  # presentation enrichment must fail open
            logger.warning(
                "Failed to resolve UI tool display names from current config: {}",
                type(exc).__name__,
            )

    return build_tool_display_name_map(
        registry,
        capabilities=capabilities,
        mcp_rows=mcp_rows,
        ui_packages=ui_packages,
    )


def enrich_tool_event_display_name(
    event: dict[str, Any],
    mapping: dict[str, str],
) -> dict[str, Any]:
    if event.get("type") not in {"tool_call", "tool_result", "tool_call_removed"}:
        return event
    tool_name = str(event.get("tool") or "").strip()
    if not tool_name:
        return event
    return {**event, "display_name": mapping.get(tool_name) or tool_name}


def enrich_deferred_display_names(
    deferred: dict[str, Any],
    mapping: dict[str, str],
) -> dict[str, Any]:
    enriched = dict(deferred)
    for key in ("calls", "approvals"):
        parts = enriched.get(key)
        if not isinstance(parts, list):
            continue
        enriched[key] = [
            {
                **part,
                "display_name": mapping.get(str(part.get("tool") or ""))
                or str(part.get("tool") or "tool"),
            }
            if isinstance(part, dict)
            else part
            for part in parts
        ]
    return enriched
