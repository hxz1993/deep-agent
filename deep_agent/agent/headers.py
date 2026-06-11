from __future__ import annotations

from fastapi import Request

from deep_agent.core.config import Settings

_DEFAULT_FORWARD_HEADERS = frozenset(
    {
        "authorization",
        "x-tenant-id",
        "x-user-token",
    }
)


def forward_header_names(settings: Settings) -> frozenset[str]:
    """Return the lowercase header names allowed for MCP forwarding."""
    raw = settings.mcp_forward_request_headers
    if not raw or not raw.strip():
        return _DEFAULT_FORWARD_HEADERS
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def extract_forward_headers(request: Request, settings: Settings) -> dict[str, str]:
    """Pick client request headers to forward into MCP tool calls."""
    allowed = forward_header_names(settings)
    return {name: value for name, value in request.headers.items() if name.lower() in allowed}
