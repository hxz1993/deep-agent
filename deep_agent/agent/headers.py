from __future__ import annotations

from collections.abc import Iterable

from fastapi import HTTPException, Request, status

from deep_agent.agent.request_context import resolve_request_context
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


def _get_header(request: Request, target: str) -> str | None:
    for name, value in request.headers.items():
        if name.lower() == target:
            return value
    return None


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
    selected = {
        name: value
        for name, value in request.headers.items()
        if _is_allowed_header(name, exact, wildcard_prefixes)
    }
    context_names = ("app", "space", "language")
    if not any(_is_allowed_header(name, exact, wildcard_prefixes) for name in context_names):
        return selected

    try:
        context = resolve_request_context(
            app=_get_header(request, "app"),
            space=_get_header(request, "space"),
            language=_get_header(request, "language"),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    selected = {
        name: value
        for name, value in selected.items()
        if name.lower() not in context_names
    }
    for name in context_names:
        value = getattr(context, name)
        if value is not None and _is_allowed_header(name, exact, wildcard_prefixes):
            selected[name] = value
    return selected
