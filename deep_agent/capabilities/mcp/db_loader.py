from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from deep_agent.capabilities.mcp.toolset_builder import build_mcp_toolset
from deep_agent.capabilities.registry import ToolPackage
from deep_agent.schemas.mcp import McpInspection

if TYPE_CHECKING:
    from deep_agent.db.models import McpServer


def tool_package_from_row(row: McpServer) -> ToolPackage:
    """Build one MCP tool package from a database row."""
    toolset = build_mcp_toolset(
        row.name,
        row.transport,
        row.config,
        inspection=getattr(row, "inspection", None),
    )
    inspection = McpInspection.from_value(
        getattr(row, "inspection", None),
        transport=row.transport,
    )
    catalog_ready = row.transport == "builtin" or (
        inspection.tools.snapshot_status == "ready"
        and inspection.tools.snapshot is not None
    )
    description = row.description or f"MCP server '{row.name}'. Tools are prefixed with '{row.name}_'."
    return ToolPackage(
        code=f"mcp:{row.name}",
        category="mcp",
        toolset=toolset,
        label=row.name,
        source="builtin" if row.transport == "builtin" else "database",
        description=description,
        tools=[],
        default_enabled=False,
        tool_catalog_status="count_only" if catalog_ready else inspection.tools.snapshot_status,
    )


def tool_packages_from_rows(rows: list[McpServer]) -> list[ToolPackage]:
    packages: list[ToolPackage] = []
    for row in rows:
        if not row.enabled:
            continue
        try:
            packages.append(tool_package_from_row(row))
        except Exception:
            logger.exception("Failed to load MCP server {!r} from database", row.name)
    return packages
