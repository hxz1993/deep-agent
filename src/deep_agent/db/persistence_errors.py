"""Bounded diagnostics for failed immutable writes; never retain message data."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any


_PART_KINDS = frozenset({
    "text", "thinking", "tool-call", "tool-return", "builtin-tool-call",
    "builtin-tool-return", "retry-prompt", "user-prompt", "system-prompt",
    "tool-availability-delta",
})
_PART_FIELDS = frozenset({
    "part_kind", "content", "args", "tool_name", "tool_call_id", "tool_kind",
    "metadata", "provider_details", "id", "signature", "provider_name",
    "outcome", "tools_added", "tools_removed",
})
_METADATA_FIELDS = frozenset({"deepagent_timing", "deepagent_plan", "overflow_handle", "ptc_diagnostics"})


def _safe_index(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _part_kind(part: Any) -> str | None:
    if not isinstance(part, Mapping):
        return None
    value = part.get("part_kind")
    return value if isinstance(value, str) and value in _PART_KINDS else "other"


def _difference(stored: Sequence[Mapping[str, Any]], incoming: Sequence[Mapping[str, Any]]):
    for old, new in zip(stored, incoming):
        if old == new:
            continue
        for key in sorted(_PART_FIELDS):
            if (key in old) != (key in new) or old.get(key) != new.get(key):
                if key == "metadata" and isinstance(old.get(key), Mapping) and isinstance(new.get(key), Mapping):
                    before, after = old[key], new[key]
                    for name in sorted(_METADATA_FIELDS):
                        if (name in before) != (name in after) or before.get(name) != after.get(name):
                            return old, new, f"metadata.{name}"
                return old, new, key
        # Never expose arbitrary user/provider-defined field names.
        return old, new, "other"
    return (stored[0] if stored else None, incoming[0] if incoming else None, "parts_count")


class ImmutableMessagePartConflict(ValueError):
    """An immutable fact changed. Only fixed-name, content-free context escapes."""

    def __init__(self, *, event_id: str, message_ordinal: Any, part_index: Any,
                 stored_parts: Sequence[Mapping[str, Any]], incoming_parts: Sequence[Mapping[str, Any]]) -> None:
        super().__init__("Immutable message part conflict")
        old, new, field = _difference(stored_parts, incoming_parts)
        self.diagnostics: dict[str, str | int | None] = {
            "persistence_reason": "immutable_part_conflict",
            "conflict_event_id": event_id if isinstance(event_id, str) and re.fullmatch(r"[0-9a-f]{32}", event_id) else None,
            "message_ordinal": _safe_index(message_ordinal),
            "part_index": _safe_index(part_index),
            "stored_part_kind": _part_kind(old),
            "incoming_part_kind": _part_kind(new),
            "conflict_field": field,
            "stored_part_count": len(stored_parts),
            "incoming_part_count": len(incoming_parts),
        }
