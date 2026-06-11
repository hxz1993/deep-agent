from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from deep_agent.capabilities.mcp.toolset_builder import build_mcp_toolset
from deep_agent.capabilities.registry import Capability
from deep_agent.core.logging import get_logger

logger = get_logger(__name__)


def _infer_transport(config: dict[str, Any]) -> str:
    if "url" in config:
        return "url"
    if "command" in config:
        return "stdio"
    raise ValueError("MCP server config must include either 'url' or 'command'")


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

    servers: dict[str, dict[str, Any]] = raw.get("mcpServers", {})
    if not servers:
        return []

    capabilities: list[Capability] = []
    for key, config in servers.items():
        try:
            transport = _infer_transport(config)
            toolset = build_mcp_toolset(key, transport, config)
        except Exception:
            logger.exception("Failed to load MCP server %r from %r", key, config_path)
            continue
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
