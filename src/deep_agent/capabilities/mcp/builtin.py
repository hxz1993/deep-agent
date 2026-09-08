from __future__ import annotations

import functools
from pathlib import Path

from loguru import logger

from deep_agent.capabilities.mcp.builtin_config import SERVER_FILENAME
from deep_agent.capabilities.registry import ToolPackage


@functools.lru_cache(maxsize=None)
def load_builtin_mcp_tool_packages(mcps_dir: str) -> list[ToolPackage]:
    """Discover builtin MCP servers shipped under ``mcps/``.

    Each immediate subdirectory containing a ``server.py`` becomes one MCP
    tool package, launched lazily as a shared HTTP server in the pod.
    Tool names are prefixed with the directory name to avoid collisions. No subprocess
    is spawned here; the agent opens
    the connection on demand (``async with agent``).
    """
    root = Path(mcps_dir)
    if not root.is_dir():
        logger.info("Builtin MCP dir {!r} not found; skipping.", mcps_dir)
        return []

    from deep_agent.capabilities.mcp.toolset_builder import build_mcp_toolset

    packages: list[ToolPackage] = []
    for entry in sorted(root.iterdir()):
        server_file = entry / SERVER_FILENAME
        if not (entry.is_dir() and server_file.is_file()):
            continue

        name = entry.name
        server = build_mcp_toolset(name, "builtin", {})
        packages.append(
            ToolPackage(
                code=f"mcp:{name}",
                category="mcp",
                toolset=server,
                label=name,
                source="builtin",
                description=f"Builtin MCP server '{name}'. Tools are prefixed with '{name}_'.",
                tools=[],
            )
        )
        logger.info("Loaded builtin MCP server {!r} ({})", name, server_file)

    return packages
