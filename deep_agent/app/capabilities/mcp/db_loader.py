from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.capabilities.registry import Capability
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.db.models import McpServer

logger = get_logger(__name__)


def capability_from_row(row: McpServer) -> Capability:
    """Build one MCP capability from a database row."""
    toolset = _build_toolset(row.name, row.transport, row.config)
    description = row.description or f"MCP server '{row.name}'. Tools are prefixed with '{row.name}_'."
    return Capability(
        name=f"mcp:{row.name}",
        kind="mcp",
        toolset=toolset,
        description=description,
        tools=[],
    )


def _build_toolset(name: str, transport: str, config: dict[str, Any]) -> Any:
    from pydantic_ai.mcp import MCPToolset

    if transport == "url":
        url = config["url"]
        headers = config.get("headers")
        toolset = MCPToolset(url, id=name, headers=headers)
    elif transport == "stdio":
        from fastmcp.client.transports import StdioTransport

        transport_obj = StdioTransport(
            command=config["command"],
            args=list(config.get("args", [])),
            env=config.get("env"),
            cwd=config.get("cwd"),
        )
        toolset = MCPToolset(transport_obj, id=name)
    else:
        raise ValueError(f"Unsupported MCP transport: {transport!r}")

    return toolset.prefixed(name)


def capabilities_from_rows(rows: list[McpServer]) -> list[Capability]:
    caps: list[Capability] = []
    for row in rows:
        if not row.enabled:
            continue
        try:
            caps.append(capability_from_row(row))
            logger.info("Loaded MCP server %r from database", row.name)
        except Exception:
            logger.exception("Failed to load MCP server %r from database", row.name)
    return caps
