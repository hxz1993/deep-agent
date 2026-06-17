"""Test MCP server that records its process id for session-scoping checks."""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("pid-echo")


def _record_path() -> Path | None:
    raw = os.environ.get("MCP_PID_RECORD")
    return Path(raw) if raw else None


@mcp.tool()
def echo_process_id() -> str:
    """Return this MCP server process id and append it to MCP_PID_RECORD if set."""
    pid = os.getpid()
    record_path = _record_path()
    if record_path is not None:
        existing: list[int] = []
        if record_path.is_file():
            raw = record_path.read_text(encoding="utf-8").strip()
            if raw:
                existing = json.loads(raw)
        existing.append(pid)
        record_path.write_text(json.dumps(existing), encoding="utf-8")
    return json.dumps({"pid": pid})


if __name__ == "__main__":
    mcp.run()
