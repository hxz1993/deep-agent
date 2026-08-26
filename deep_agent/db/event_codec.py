from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, uuid5

from pydantic_ai.messages import ModelMessage

from deep_agent.db.serialization import deserialize_message, serialize_message


EVENT_SCHEMA_VERSION = 1
MAX_SEARCH_TEXT_CHARS = 65_536
MESSAGE_EVENT_TYPES = frozenset(
    {"user/message", "assistant/message", "tool/results", "model/request"}
)
_TOOL_RESULT_PART_KINDS = frozenset(
    {"tool-return", "builtin-tool-return", "retry-prompt", "tool-availability-delta"}
)
_SENSITIVE_SEARCH_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "headers",
        "metadata",
        "provider_details",
        "request_headers",
        "signature",
        "token",
    }
)
_WHITESPACE_RE = re.compile(r"\s+")


class EventCodecError(ValueError):
    pass


@dataclass(frozen=True)
class MessageEventDraft:
    event_id: str
    event_type: str
    search_text: str | None
    payload: dict[str, Any]
    schema_version: int = EVENT_SCHEMA_VERSION


def encode_messages(
    messages: Sequence[ModelMessage],
    *,
    session_id: str,
    run_id: str | None,
    first_message_ordinal: int = 0,
) -> list[MessageEventDraft]:
    """Encode one complete Pydantic ModelMessage into one durable Event."""

    drafts: list[MessageEventDraft] = []
    for offset, message in enumerate(messages):
        serialized = serialize_message(message)
        ordinal = first_message_ordinal + offset
        event_type = event_type_for_message(serialized)
        drafts.append(
            MessageEventDraft(
                event_id=deterministic_message_event_id(
                    session_id=session_id,
                    run_id=run_id,
                    message_ordinal=ordinal,
                ),
                event_type=event_type,
                search_text=build_search_text(event_type, serialized),
                payload={"message": serialized},
            )
        )
    return drafts


def decode_message_events(events: Sequence[Any]) -> list[ModelMessage]:
    messages: list[ModelMessage] = []
    for event in sorted(events, key=lambda item: int(_get(item, "sequence"))):
        event_type = str(_get(event, "event_type"))
        if event_type not in MESSAGE_EVENT_TYPES:
            continue
        schema_version = int(_get(event, "schema_version"))
        if schema_version != EVENT_SCHEMA_VERSION:
            raise EventCodecError(
                f"Unsupported session event schema_version={schema_version}"
            )
        payload = _get(event, "payload")
        if not isinstance(payload, Mapping):
            raise EventCodecError("Message Event payload must be an object")
        serialized = payload.get("message")
        if not isinstance(serialized, dict):
            raise EventCodecError("Message Event is missing payload.message")
        try:
            messages.append(deserialize_message(serialized))
        except Exception as exc:
            event_id = _get_optional(event, "event_id") or "unknown"
            raise EventCodecError(
                f"Message Event {event_id} cannot be restored"
            ) from exc
    return messages


def deterministic_message_event_id(
    *, session_id: str, run_id: str | None, message_ordinal: int
) -> str:
    """Stable idempotency key without a second persisted Message ID column."""

    return uuid5(
        NAMESPACE_URL,
        f"deep-agent-message:{session_id}:{run_id or 'unscoped'}:{message_ordinal}",
    ).hex


def event_type_for_message(message: Mapping[str, Any]) -> str:
    kind = str(message.get("kind") or "")
    parts = message.get("parts")
    part_items = parts if isinstance(parts, list) else []
    if kind == "response":
        return "assistant/message"
    if any(
        isinstance(part, Mapping) and part.get("part_kind") == "user-prompt"
        for part in part_items
    ):
        return "user/message"
    if any(
        isinstance(part, Mapping) and part.get("part_kind") in _TOOL_RESULT_PART_KINDS
        for part in part_items
    ):
        return "tool/results"
    return "model/request"


def message_payload(value: Any) -> dict[str, Any] | None:
    payload = _get_optional(value, "payload")
    if not isinstance(payload, Mapping):
        return None
    message = payload.get("message")
    return dict(message) if isinstance(message, Mapping) else None


def message_parts(value: Any) -> list[dict[str, Any]]:
    message = message_payload(value)
    if message is None:
        return []
    parts = message.get("parts")
    return [dict(part) for part in parts or [] if isinstance(part, Mapping)]


def tool_part_references(value: Any) -> list[dict[str, Any]]:
    """Return derived call/result locations without duplicate SQL columns."""

    references: list[dict[str, Any]] = []
    for part_offset, part in enumerate(message_parts(value)):
        call_id = part.get("tool_call_id")
        if not call_id:
            continue
        part_kind = str(part.get("part_kind") or "")
        if part_kind in {"tool-call", "builtin-tool-call"}:
            role = "call"
        elif part_kind in {"tool-return", "builtin-tool-return", "retry-prompt"}:
            role = "result"
        else:
            continue
        references.append(
            {
                "part_offset": part_offset,
                "role": role,
                "call_id": str(call_id),
                "tool_name": str(part.get("tool_name") or "") or None,
                "part": part,
            }
        )
    return references


def attachment_part_references(value: Any) -> list[dict[str, Any]]:
    """Return attachment locations inside a serialized user prompt."""

    references: list[dict[str, Any]] = []
    for part_offset, part in enumerate(message_parts(value)):
        if part.get("part_kind") != "user-prompt":
            continue
        content = part.get("content")
        if not isinstance(content, list):
            continue
        for item_offset, item in enumerate(content):
            if not isinstance(item, Mapping) or item.get("kind") != "attachment-ref":
                continue
            file_id = item.get("id")
            if not file_id:
                continue
            references.append(
                {
                    "part_offset": part_offset,
                    "item_offset": item_offset,
                    "file_id": str(file_id),
                    "name": str(item.get("name") or "") or None,
                    "mime": str(item.get("mime") or "") or None,
                    "item": dict(item),
                }
            )
    return references


def event_contains_call(value: Any, call_id: str) -> bool:
    return any(item["call_id"] == call_id for item in tool_part_references(value))


def event_contains_tool(value: Any, tool_name: str) -> bool:
    return any(item["tool_name"] == tool_name for item in tool_part_references(value))


def build_search_text(event_type: str, message: Mapping[str, Any]) -> str | None:
    safe_message = dict(message)
    parts = safe_message.get("parts")
    if isinstance(parts, list):
        safe_message["parts"] = [
            part
            for part in parts
            if not (
                isinstance(part, Mapping)
                and part.get("part_kind") == "thinking"
            )
        ]
    safe = _search_projection(safe_message)
    if safe in (None, "", [], {}):
        return None
    normalized = normalize_search_text(
        json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    return normalized[:MAX_SEARCH_TEXT_CHARS] or None


def normalize_search_text(value: str) -> str:
    return _WHITESPACE_RE.sub(
        " ", unicodedata.normalize("NFKC", value).casefold()
    ).strip()


def payload_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _search_projection(value: Any, *, key: str | None = None) -> Any:
    if key is not None and key.casefold() in _SENSITIVE_SEARCH_KEYS:
        return None
    if isinstance(value, Mapping):
        return {
            str(child_key): child_value
            for child_key, child in value.items()
            if (child_value := _search_projection(child, key=str(child_key)))
            not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [
            child_value
            for child in value
            if (child_value := _search_projection(child)) not in (None, "", [], {})
        ]
    if isinstance(value, (str, int, float, bool)):
        return value
    return None


def _get(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item[name]
    return getattr(item, name)


def _get_optional(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)
