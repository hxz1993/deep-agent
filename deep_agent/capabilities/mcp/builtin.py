from __future__ import annotations

import functools
from pathlib import Path

from loguru import logger

from deep_agent.capabilities.mcp.builtin_config import SERVER_FILENAME
from deep_agent.capabilities.registry import Capability


@functools.lru_cache(maxsize=None)
def load_builtin_mcp_capabilities(mcps_dir: str) -> list[Capability]:
    """Discover builtin MCP servers shipped under ``mcps/``.

    Each immediate subdirectory containing a ``server.py`` becomes one MCP
    capability, launched lazily as a shared HTTP server in the pod.
    Tool names are prefixed with the directory name to avoid collisions. No subprocess
    is spawned here; the agent opens
    the connection on demand (``async with agent``).
    """
    root = Path(mcps_dir)
    if not root.is_dir():
        logger.info("Builtin MCP dir {!r} not found; skipping.", mcps_dir)
        return []

    from deep_agent.capabilities.mcp.toolset_builder import build_mcp_toolset

    capabilities: list[Capability] = []
    for entry in sorted(root.iterdir()):
        server_file = entry / SERVER_FILENAME
        if not (entry.is_dir() and server_file.is_file()):
            continue

        name = entry.name
        server = build_mcp_toolset(name, "builtin", {})
        capabilities.append(
            Capability(
                name=f"mcp:{name}",
                kind="mcp",
                toolset=server,
                description=f"Builtin MCP server '{name}'. Tools are prefixed with '{name}_'.",
                tools=[],
                default_enabled=False,
            )
        )
        logger.info("Loaded builtin MCP server {!r} ({})", name, server_file)

    return capabilities
