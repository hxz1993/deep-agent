"""Shared CLI entry for builtin FastMCP servers (stdio or streamable-http)."""

from __future__ import annotations

import _thread
import argparse
import os
import sys
import threading
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from deep_agent.core.mcp_constants import MCP_REGISTRY_JSON_ENV, MCP_SERVER_TTL_ENV


def _registry_mtime_age(path: Path) -> float | None:
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return None


def should_exit_on_registry_ttl() -> bool:
    """Return True when the registry json has not been touched within TTL."""
    raw_path = os.environ.get(MCP_REGISTRY_JSON_ENV)
    raw_ttl = os.environ.get(MCP_SERVER_TTL_ENV)
    if not raw_path or not raw_ttl:
        return False
    try:
        ttl = float(raw_ttl)
    except ValueError:
        return False
    if ttl <= 0:
        return False
    age = _registry_mtime_age(Path(raw_path))
    if age is None:
        return True
    return age >= ttl


def _registry_ttl_watcher() -> None:
    raw_ttl = os.environ.get(MCP_SERVER_TTL_ENV, "300")
    print(f"mcp server will exit after idle for {raw_ttl} seconds")
    try:
        ttl = max(1.0, float(raw_ttl))
    except ValueError:
        ttl = 300.0
    interval = max(1.0, ttl / 5)
    while True:
        time.sleep(interval)
        if should_exit_on_registry_ttl():
            raw_path = os.environ.get(MCP_REGISTRY_JSON_ENV, "?")
            age = _registry_mtime_age(Path(raw_path)) if raw_path != "?" else None
            age_text = "missing" if age is None else f"{age:.0f}s"
            print(
                f"[mcp] shutting down: registry {raw_path} idle {age_text} >= ttl {ttl:.0f}s",
                flush=True,
            )
            _thread.interrupt_main()
            return


def start_registry_ttl_watcher() -> None:
    """Start a daemon thread that stops the server when registry touch goes stale."""
    print(f"MCP_REGISTRY_JSON_ENV={os.environ.get(MCP_REGISTRY_JSON_ENV)}", file=sys.stderr)
    if not os.environ.get(MCP_REGISTRY_JSON_ENV):
        return
    threading.Thread(
        target=_registry_ttl_watcher,
        name="mcp-registry-ttl",
        daemon=True,
    ).start()


def run_streamable_http(mcp: FastMCP) -> None:
    """Run streamable-http with registry TTL watcher (see debug/t.py)."""
    start_registry_ttl_watcher()
    try:
        mcp.run(transport="streamable-http")
    except KeyboardInterrupt:
        print(f"[mcp] exited {mcp.name!r}", flush=True)


def run_fastmcp(mcp: FastMCP, *, prog: str | None = None) -> None:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.transport == "streamable-http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        run_streamable_http(mcp)
    else:
        mcp.run(transport="stdio")
