"""Test-only builtin MCP used by real subprocess integration tests."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("echo-fixture")


@mcp.tool()
def reverse(text: str) -> str:
    """Reverse the characters in the supplied text."""
    return text[::-1]


if __name__ == "__main__":
    from mcps._run import run_fastmcp

    run_fastmcp(mcp, prog="echo_fixture")
