"""Request-scoped context forwarded into MCP tool invocations.

HTTP MCP servers receive merged headers on the transport connection.

Stdio / builtin MCP servers receive the same headers in two ways:

1. **Subprocess startup** — JSON in :data:`~deep_agent.lib.const.REQUEST_HEADERS_ENV` (first
   request in a session-scoped pool; each run when session scoping is off).
   Individual servers decide whether and how to consume this env var.
2. **Per tool call** — MCP ``_meta`` on each invocation:

    meta = ctx.request_context.meta  # FastMCP Context in mcps/*/server.py
    headers = getattr(meta, "headers", None) or {}
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.mcp import CallToolFunc

from deep_agent.agent.deps import AgentDeps
from deep_agent.lib.const import REQUEST_HEADERS_ENV


async def inject_request_meta(
    ctx: RunContext[AgentDeps],
    call_tool: CallToolFunc,
    name: str,
    args: dict[str, Any],
) -> Any:
    """Attach inbound HTTP headers to each MCP tool call via ``_meta``."""
    metadata = None
    if ctx.deps.request_headers:
        metadata = {"headers": dict(ctx.deps.request_headers)}
    return await call_tool(name, args, metadata=metadata)


def merge_mcp_headers(
    static_headers: dict[str, str] | None,
    request_headers: dict[str, str] | None,
) -> dict[str, str] | None:
    """Merge configured MCP headers with per-request forwarded headers."""
    merged: dict[str, str] = {}
    if static_headers:
        merged.update(static_headers)
    if request_headers:
        merged.update(request_headers)
    return merged or None


def serialize_request_headers(request_headers: Mapping[str, str]) -> str:
    """Encode forwarded HTTP headers for a stdio MCP subprocess env var."""
    return json.dumps(dict(request_headers), ensure_ascii=False)


def apply_request_headers_to_stdio_config(
    config: dict[str, Any],
    request_headers: Mapping[str, str] | None,
) -> dict[str, Any]:
    """Return a stdio launch config with request headers injected into ``env``.

    Headers are written to :data:`~deep_agent.lib.const.REQUEST_HEADERS_ENV` unless that key is already
    set in ``config.env`` (static DB config wins). When session-scoped pooling is
    enabled, only the first acquire for a session uses the headers from that run.
    """
    if not request_headers:
        return config

    existing_env = dict(config.get("env") or {})
    if REQUEST_HEADERS_ENV in existing_env:
        return config

    merged = dict(config)
    env = dict(existing_env)
    env[REQUEST_HEADERS_ENV] = serialize_request_headers(request_headers)
    merged["env"] = env
    return merged
