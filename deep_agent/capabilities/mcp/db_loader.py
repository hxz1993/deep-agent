from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from deep_agent.capabilities.mcp.toolset_builder import build_mcp_toolset
from deep_agent.capabilities.registry import Capability

if TYPE_CHECKING:
    from deep_agent.db.models import McpServer


def capability_from_row(row: McpServer) -> Capability:
    """Build one MCP capability from a database row."""
    toolset = build_mcp_toolset(row.name, row.transport, row.config)
    description = row.description or f"MCP server '{row.name}'. Tools are prefixed with '{row.name}_'."
    return Capability(
        name=f"mcp:{row.name}",
        kind="mcp",
        toolset=toolset,
        description=description,
        tools=[],
    )


def capabilities_from_rows(rows: list[McpServer]) -> list[Capability]:
    caps: list[Capability] = []
    for row in rows:
        if not row.enabled:
            continue
        try:
            caps.append(capability_from_row(row))
            logger.info("Loaded MCP server {!r} from database", row.name)
        except Exception:
            logger.exception("Failed to load MCP server {!r} from database", row.name)
    return caps
