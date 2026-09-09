from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

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
    "context deadline exceeded",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
)

_MCP_REQUEST_TIMEOUT = -32001
_CANVAS_ERROR_PREFIX = "CANVAS_ERROR "
_LEGACY_CANVAS_DEADLINE_MARKERS = (
    "wait for canvas response",
    "context deadline exceeded",
)


@dataclass(frozen=True)
class TerminalMcpRequestFailure:
    """Safe classification details for a non-correctable MCP request failure."""

    canvas_outcome_unknown: bool = False


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


def _parse_canvas_error(text: str) -> dict[str, Any] | None:
    marker_at = text.find(_CANVAS_ERROR_PREFIX)
    if marker_at < 0:
        return None
    raw_payload = text[marker_at + len(_CANVAS_ERROR_PREFIX) :].lstrip()
    try:
        payload, _ = json.JSONDecoder().raw_decode(raw_payload)
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def classify_terminal_mcp_request_failure(
    exc: BaseException,
) -> TerminalMcpRequestFailure | None:
    """Classify an MCP retry that cannot be corrected by changing arguments.

    Pydantic AI intentionally maps MCP protocol failures to ``ModelRetry``.  A
    request timeout or transport outage is not correctable by changing tool
    arguments, so DeepAgent must turn only those cases into a failed tool result
    while leaving ordinary MCP validation/business retries untouched.
    """
    for item in _walk_exception_tree(exc):
        if isinstance(item, (asyncio.TimeoutError, httpx.TimeoutException, httpx.NetworkError)):
            return TerminalMcpRequestFailure()
        if isinstance(item, httpx.HTTPStatusError) and item.response.status_code >= 500:
            return TerminalMcpRequestFailure()
        if isinstance(item, McpError) and item.error.code == _MCP_REQUEST_TIMEOUT:
            return TerminalMcpRequestFailure()

        original_text = str(item)
        canvas_error = _parse_canvas_error(original_text)
        if canvas_error is not None:
            if canvas_error.get("retryable") is False:
                return TerminalMcpRequestFailure(
                    canvas_outcome_unknown=canvas_error.get("outcome") == "unknown"
                )
            # A valid structured Canvas error is authoritative.  Correctable
            # errors must remain ModelRetry even if their free text mentions a
            # timeout-like business condition.
            return None

        normalized_text = original_text.lower()
        if all(marker in normalized_text for marker in _LEGACY_CANVAS_DEADLINE_MARKERS):
            return TerminalMcpRequestFailure(canvas_outcome_unknown=True)
        if any(marker in normalized_text for marker in _RECOVERABLE_MARKERS):
            return TerminalMcpRequestFailure()
        if any(marker in normalized_text for marker in _TERMINAL_REQUEST_MARKERS):
            return TerminalMcpRequestFailure()
    return None
