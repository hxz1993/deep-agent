"""Request-scoped context forwarded into MCP tool invocations.

HTTP MCP servers receive merged headers on the transport connection and, for
builtin pooled clients, per-tool ``_meta`` via :func:`inject_request_meta`.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.mcp import CallToolFunc

from deep_agent.agent.deps import AgentDeps
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
