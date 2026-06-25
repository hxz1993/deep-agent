"""Example builtin MCP server: a few small text utilities.

This is a standalone stdio MCP server. It does NOT import anything from the
``app`` package, so it can run as its own subprocess (the agent launches it via
``MCPServerStdio``). Keep builtin servers self-contained like this.

Authoring pattern for a builtin server:
  1. Drop a ``server.py`` under ``mcps/<name>/``.
  2. Build a ``FastMCP`` instance and register tools with ``@mcp.tool()``.
  3. Run it over stdio in ``__main__`` (``mcp.run()`` defaults to stdio).

The loader (``app/capabilities/mcp/builtin.py``) auto-discovers every
``mcps/<name>/server.py`` and exposes it as the capability ``mcp:<name>``.
Tool names are prefixed with ``<name>_`` to avoid collisions.

Run it directly for a quick sanity check:
    python mcps/text_tools/server.py
"""

from __future__ import annotations

import re
import unicodedata

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("text-tools")


@mcp.tool()
def word_count(text: str) -> int:
    """Count the number of whitespace-separated words in ``text``."""
    return len(text.split())


@mcp.tool()
def char_count(text: str, include_whitespace: bool = True) -> int:
    """Count characters in ``text``. Set ``include_whitespace=False`` to ignore spaces."""
    if include_whitespace:
        return len(text)
    return len("".join(text.split()))


@mcp.tool()
def slugify(text: str) -> str:
    """Turn ``text`` into a URL-friendly slug (lowercase, ASCII, hyphen-separated)."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = ascii_text.lower().strip()
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text)
    return ascii_text.strip("-")


@mcp.tool()
def reverse(text: str) -> str:
    """Reverse the characters in ``text``."""
    return text[::-1]


if __name__ == "__main__":
    from mcps._run import run_fastmcp

    run_fastmcp(mcp, prog="text_tools")
