from __future__ import annotations

import sys
from pathlib import Path

from deep_agent.capabilities.registry import Capability
from deep_agent.core.logging import get_logger

logger = get_logger(__name__)

# A builtin server is any ``mcps/<name>/`` directory holding this entrypoint.
SERVER_FILENAME = "server.py"


def load_builtin_mcp_capabilities(mcps_dir: str) -> list[Capability]:
    """Discover builtin MCP servers shipped under ``mcps/``.

    Each immediate subdirectory containing a ``server.py`` becomes one MCP
    capability, launched lazily over stdio with the current Python interpreter.
    Tool names are prefixed with the directory name to avoid collisions, mirroring
    the ``config/mcp.json`` loader. No subprocess is spawned here; the agent opens
    the connection on demand (``async with agent``).
    """
    root = Path(mcps_dir)
    if not root.is_dir():
        logger.info("Builtin MCP dir %r not found; skipping.", mcps_dir)
        return []

    from deep_agent.capabilities.mcp.toolset_builder import build_mcp_toolset

    capabilities: list[Capability] = []
    for entry in sorted(root.iterdir()):
        server_file = entry / SERVER_FILENAME
        if not (entry.is_dir() and server_file.is_file()):
            continue

        name = entry.name
        server = build_mcp_toolset(
            name,
            "stdio",
            {"command": sys.executable, "args": [str(server_file)]},
        )
        capabilities.append(
            Capability(
                name=f"mcp:{name}",
                kind="mcp",
                toolset=server,
                description=f"Builtin MCP server '{name}'. Tools are prefixed with '{name}_'.",
                tools=[],
            )
        )
        logger.info("Loaded builtin MCP server %r (%s)", name, server_file)

    return capabilities
