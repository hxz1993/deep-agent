from __future__ import annotations

from collections.abc import Iterable
from fastapi import Request

from deep_agent.core.config import Settings

_DEFAULT_FORWARD_HEADERS = frozenset(
    {
        "authorization",
        "app",
        "space",
        "cookie",
        "user",
        "language",
        "x-deepfos-*",
        "x-tenant-id",
        "x-user-token",
    }
)


def forward_header_names(settings: Settings) -> frozenset[str]:
    """Return lowercase allowlist entries for MCP request header forwarding."""
    raw = settings.mcp_forward_request_headers
    if not raw or not raw.strip():
        return _DEFAULT_FORWARD_HEADERS
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def _split_rules(allowed: Iterable[str]) -> tuple[frozenset[str], tuple[str, ...]]:
    exact: set[str] = set()
    wildcard_prefixes: list[str] = []
    for item in allowed:
        if item.endswith("*"):
            wildcard_prefixes.append(item[:-1])
        else:
            exact.add(item)
    return frozenset(exact), tuple(wildcard_prefixes)


def _is_allowed_header(name: str, exact: frozenset[str], wildcard_prefixes: tuple[str, ...]) -> bool:
    lowered = name.lower()
    if lowered in exact:
        return True
    return any(lowered.startswith(prefix) for prefix in wildcard_prefixes)


def extract_forward_headers(request: Request, settings: Settings) -> dict[str, str]:
    """Pick client request headers to forward into MCP tool calls.

    - Case-insensitive by normalizing header names to lowercase for matching.
    - Supports suffix wildcard rules like ``x-deepfos-*`` in the allowlist.
    """
    allowed = forward_header_names(settings)
    exact, wildcard_prefixes = _split_rules(allowed)
    return {
        name: value
        for name, value in request.headers.items()
        if _is_allowed_header(name, exact, wildcard_prefixes)
    }
