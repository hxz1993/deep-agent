from __future__ import annotations

_RECOVERABLE_MARKERS = (
    "failed to connect",
    "failed to initialize server session",
    "peer closed connection",
    "connection closed",
    "incomplete chunked",
    "broken pipe",
    "connection reset",
)


def is_recoverable_mcp_disconnect(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _RECOVERABLE_MARKERS)
