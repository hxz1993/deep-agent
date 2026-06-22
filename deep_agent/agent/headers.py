from __future__ import annotations

from collections.abc import Iterable

from fastapi import Request

from deep_agent.options import OPTION


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


def extract_forward_headers(
    request: Request,
    allowed: frozenset[str] | None = None,
) -> dict[str, str]:
    """Pick client request headers to forward into MCP tool calls.

    - Case-insensitive by normalizing header names to lowercase for matching.
    - Supports suffix wildcard rules like ``x-deepfos-*`` in the allowlist.
    """
    if allowed is None:
        allowed = OPTION.agent.mcp_forward_request_headers
    exact, wildcard_prefixes = _split_rules(allowed)
    return {
        name: value
        for name, value in request.headers.items()
        if _is_allowed_header(name, exact, wildcard_prefixes)
    }
