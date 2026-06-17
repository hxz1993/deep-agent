"""Minimal MCP server used by integration tests to verify header forwarding."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP("header-echo")

# Keep in sync with deep_agent.lib.const.REQUEST_HEADERS_ENV (standalone subprocess).
_STARTUP_HEADERS_RAW = os.environ.get("DEEP_AGENT_MCP_REQUEST_HEADERS")


def _normalize_headers(headers: dict[str, str]) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _headers_from_meta(ctx: Context) -> dict[str, str]:
    request_context = ctx.request_context
    if request_context is None or request_context.meta is None:
        return {}

    raw = getattr(request_context.meta, "headers", None)
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return _normalize_headers(raw)
    if hasattr(raw, "model_dump"):
        dumped = raw.model_dump()
        return _normalize_headers({str(k): str(v) for k, v in dumped.items()})
    return _normalize_headers({str(k): str(v) for k, v in dict(raw).items()})


def _headers_from_http_request(ctx: Context) -> dict[str, str]:
    request_context = ctx.request_context
    if request_context is None:
        return {}
    request = getattr(request_context, "request", None)
    if request is None:
        return {}
    return _normalize_headers(dict(request.headers.items()))


def _filter_forwarded(headers: dict[str, str]) -> dict[str, str]:
    """Keep only headers that deep-agent is expected to forward."""
    out: dict[str, str] = {}
    for key, value in headers.items():
        if key == "authorization" or key.startswith("x-"):
            out[key] = value
    return out


def _record_headers(headers: dict[str, str]) -> None:
    record_path = os.environ.get("MCP_HEADER_RECORD")
    if record_path:
        Path(record_path).write_text(json.dumps(headers, sort_keys=True), encoding="utf-8")


@mcp.tool()
def echo_startup_headers() -> str:
    """Return HTTP headers injected at subprocess startup (see deep_agent.lib.const)."""
    return _STARTUP_HEADERS_RAW or "{}"


@mcp.tool()
def echo_request_headers(ctx: Context) -> str:
    """Return HTTP headers forwarded by deep-agent (stdio via _meta, HTTP via connection)."""
    headers = _filter_forwarded(_headers_from_meta(ctx) or _headers_from_http_request(ctx))
    _record_headers(headers)
    return json.dumps(headers, sort_keys=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.transport == "streamable-http":
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
    sys.exit(0)
