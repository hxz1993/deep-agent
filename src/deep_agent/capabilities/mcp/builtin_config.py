from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from deep_agent.core.paths import DEEP_AGENT_ROOT, MCP_SERVERS_ROOT

SERVER_FILENAME = "server.py"
MCPS_DIR = MCP_SERVERS_ROOT


def builtin_server_path(name: str) -> Path:
    """Return ``mcp_servers/<name>/server.py`` if it exists."""
    return MCPS_DIR / name / SERVER_FILENAME


def is_shipped_builtin(name: str) -> bool:
    return builtin_server_path(name).is_file()


def list_builtin_server_names() -> list[str]:
    """Sorted names of shipped ``mcp_servers/<name>/server.py`` builtins."""
    if not MCPS_DIR.is_dir():
        return []
    names: list[str] = []
    for entry in sorted(MCPS_DIR.iterdir()):
        if entry.is_dir() and (entry / SERVER_FILENAME).is_file():
            names.append(entry.name)
    return names


def is_builtin_mcp_row(name: str, transport: str, config: dict[str, Any]) -> bool:
    """Repo-shipped ``mcp_servers/<name>/`` servers never participate in UI tool discovery."""
    return transport == "builtin"


def resolve_builtin_spawn_config(name: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve spawn metadata for a shared-HTTP builtin MCP server."""
    server_file = builtin_server_path(name)
    if not server_file.is_file():
        raise ValueError(
            f"Builtin MCP server {name!r} not found at {server_file}. "
            f"Expected mcp_servers/{name}/{SERVER_FILENAME} under {DEEP_AGENT_ROOT}."
        )

    cfg = dict(config or {})
    resolved: dict[str, Any] = {
        "command": sys.executable,
        "args": [str(server_file)],
    }
    if cfg.get("env"):
        resolved["env"] = dict(cfg["env"])
    if cfg.get("cwd"):
        resolved["cwd"] = cfg["cwd"]
    return resolved


def normalize_mcp_config(name: str, transport: str, config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Normalize DB/API config to a concrete transport + connection config."""
    if transport == "stdio":
        raise ValueError("stdio MCP transport is no longer supported; use url or builtin")
    if transport in ("builtin", "stdio"):
        return "builtin", resolve_builtin_spawn_config(name, config)
    return transport, config
