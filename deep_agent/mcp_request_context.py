"""Transport-neutral MCP request-context policy helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

McpRequestContextMode = Literal["inherit", "isolated"]
DEFAULT_MCP_REQUEST_CONTEXT_MODE: McpRequestContextMode = "inherit"
NEW_URL_MCP_REQUEST_CONTEXT_MODE: McpRequestContextMode = "isolated"


def resolve_mcp_request_context_mode(
    transport: str,
    config: Mapping[str, Any] | None,
) -> McpRequestContextMode:
    """Resolve the effective policy without rewriting legacy database rows."""
    if transport == "builtin":
        return "inherit"
    raw = (config or {}).get("request_context")
    if isinstance(raw, Mapping) and raw.get("mode") == "isolated":
        return "isolated"
    return DEFAULT_MCP_REQUEST_CONTEXT_MODE


def resolve_mcp_headers(
    transport: str,
    config: Mapping[str, Any] | None,
    request_headers: Mapping[str, str] | None,
) -> dict[str, str] | None:
    """Apply the configured policy to static and request-scoped headers."""
    static_headers = dict((config or {}).get("headers") or {})
    if resolve_mcp_request_context_mode(transport, config) == "isolated":
        return static_headers or None
    merged = {**static_headers, **dict(request_headers or {})}
    return merged or None
