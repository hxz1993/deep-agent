from __future__ import annotations

import asyncio
from collections.abc import Iterator

import httpx
from mcp import McpError


_RECOVERABLE_MARKERS = (
    "failed to connect",
    "failed to initialize server session",
    "peer closed connection",
    "connection closed",
    "incomplete chunked",
    "broken pipe",
    "connection reset",
)

_TERMINAL_REQUEST_MARKERS = (
    "request timed out",
    "timed out while waiting for response",
    "execution timed out",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
)

_MCP_REQUEST_TIMEOUT = -32001


def _walk_exception_tree(exc: BaseException) -> Iterator[BaseException]:
    """Yield grouped and chained exceptions once without exposing their text."""
    pending = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        elif current.__context__ is not None:
            pending.append(current.__context__)


def is_recoverable_mcp_disconnect(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _RECOVERABLE_MARKERS)


def is_terminal_mcp_request_failure(exc: BaseException) -> bool:
    """Return whether an MCP retry actually represents unavailable upstream I/O.

    Pydantic AI intentionally maps MCP protocol failures to ``ModelRetry``.  A
    request timeout or transport outage is not correctable by changing tool
    arguments, so DeepAgent must turn only those cases into a failed tool result
    while leaving ordinary MCP validation/business retries untouched.
    """
    for item in _walk_exception_tree(exc):
        if isinstance(item, (asyncio.TimeoutError, httpx.TimeoutException, httpx.NetworkError)):
            return True
        if isinstance(item, httpx.HTTPStatusError) and item.response.status_code >= 500:
            return True
        if isinstance(item, McpError) and item.error.code == _MCP_REQUEST_TIMEOUT:
            return True

        text = str(item).lower()
        if any(marker in text for marker in _RECOVERABLE_MARKERS):
            return True
        if any(marker in text for marker in _TERMINAL_REQUEST_MARKERS):
            return True
    return False
