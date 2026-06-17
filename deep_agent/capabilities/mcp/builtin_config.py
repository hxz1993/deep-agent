from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from deep_agent.core.paths import DEEP_AGENT_ROOT

SERVER_FILENAME = "server.py"
MCPS_DIR = DEEP_AGENT_ROOT / "mcps"


def builtin_server_path(name: str) -> Path:
    """Return ``mcps/<name>/server.py`` if it exists."""
    return MCPS_DIR / name / SERVER_FILENAME


def is_shipped_builtin(name: str) -> bool:
    return builtin_server_path(name).is_file()


def is_legacy_builtin_stdio_config(name: str, config: dict[str, Any]) -> bool:
    """Detect stdio rows that point at a repo-shipped ``mcps/<name>/server.py``."""
    if not is_shipped_builtin(name):
        return False
    if config.get("source") == "builtin":
        return True
    args = config.get("args") or []
    if len(args) == 1 and Path(str(args[0])).name == SERVER_FILENAME:
        return True
    return "command" not in config and "args" not in config


def resolve_builtin_stdio_config(name: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve a portable builtin config to a concrete stdio launch config."""
    server_file = builtin_server_path(name)
    if not server_file.is_file():
        raise ValueError(
            f"Builtin MCP server {name!r} not found at {server_file}. "
            f"Expected mcps/{name}/{SERVER_FILENAME} under {DEEP_AGENT_ROOT}."
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
    """Normalize DB/API config to a concrete transport + stdio/url config."""
    if transport == "builtin":
        return "stdio", resolve_builtin_stdio_config(name, config)

    if transport == "stdio" and is_legacy_builtin_stdio_config(name, config):
        portable = {k: config[k] for k in ("env", "cwd") if k in config}
        return "stdio", resolve_builtin_stdio_config(name, portable)

    return transport, config
