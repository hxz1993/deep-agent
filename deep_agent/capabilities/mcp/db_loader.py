from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from deep_agent.capabilities.mcp.toolset_builder import build_mcp_toolset
from deep_agent.capabilities.registry import ToolPackage

if TYPE_CHECKING:
    from deep_agent.db.models import McpServer


def tool_package_from_row(row: McpServer) -> ToolPackage:
    """Build one MCP tool package from a database row."""
    toolset = build_mcp_toolset(row.name, row.transport, row.config)
    description = row.description or f"MCP server '{row.name}'. Tools are prefixed with '{row.name}_'."
    return ToolPackage(
        code=f"mcp:{row.name}",
        category="mcp",
        toolsets=[toolset],
        label=row.name,
        source="builtin" if row.transport == "builtin" else "database",
        description=description,
        tools=[],
        default_enabled=False,
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
