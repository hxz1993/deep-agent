from __future__ import annotations

import json
from pathlib import Path

from app.capabilities.registry import Capability
from app.core.logging import get_logger

logger = get_logger(__name__)


def load_mcp_capabilities(config_path: str) -> list[Capability]:
    """Load MCP servers declared in a Claude-style ``mcpServers`` JSON file.

    Each server becomes one capability. Tool names are prefixed with the server
    key to avoid collisions across servers. Connections are opened lazily by the
    agent (``async with agent``), so this does not perform any network I/O.
    """
    path = Path(config_path)
    if not path.is_file():
        logger.info("MCP config %r not found; skipping MCP loading.", config_path)
        return []

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.exception("Invalid JSON in MCP config %r", config_path)
        return []

    server_keys = list(raw.get("mcpServers", {}).keys())
    if not server_keys:
        return []

    # Imported lazily so the app starts even if the optional mcp extra is absent.
    from pydantic_ai.mcp import load_mcp_toolsets

    # Each toolset is already wrapped in a PrefixedToolset using the server name.
    toolsets = load_mcp_toolsets(str(path))
    capabilities: list[Capability] = []
    for key, toolset in zip(server_keys, toolsets):
        capabilities.append(
            Capability(
                name=f"mcp:{key}",
                kind="mcp",
                toolset=toolset,
                description=f"MCP server '{key}'. Tools are prefixed with '{key}_'.",
                tools=[],
            )
        )
        logger.info("Loaded MCP server %r", key)

    return capabilities
