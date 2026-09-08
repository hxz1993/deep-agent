from __future__ import annotations

import hashlib
from copy import deepcopy
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, uuid5

from pydantic_ai.messages import ModelMessage

from deep_agent.db.serialization import deserialize_message, serialize_message


EVENT_SCHEMA_VERSION = 1
# SQLAlchemy ``Text`` maps to MySQL ``TEXT``, whose limit is 65,535 bytes.
# Search text can contain multi-byte Unicode, so a character limit is unsafe.
MAX_SEARCH_TEXT_BYTES = 65_000
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


def encode_message_parts(
    messages: Sequence[ModelMessage], *, session_id: str, run_id: str,
    first_message_ordinal: int = 1,
) -> list[MessageEventDraft]:
    """Insert confirmed immutable parts, retaining their SDK message boundary."""
    drafts: list[MessageEventDraft] = []
    for offset, message in enumerate(messages):
        serialized = serialize_message(message)
        metadata = dict(serialized.get("metadata") or {})
        ordinal = int(metadata.pop("deepagent_message_ordinal", first_message_ordinal + offset))
        indices = metadata.pop("deepagent_part_indices", None)
        serialized["metadata"] = metadata or None
        parts = serialized.get("parts") or []
        # The first input is already durable before SDK execution starts.
        has_results = any(part.get("part_kind") in _TOOL_RESULT_PART_KINDS for part in parts)
        if event_type_for_message(serialized) == "user/message" and not has_results:
            drafts.extend(encode_messages(
                [deserialize_message(serialized)], session_id=session_id,
                run_id=run_id, first_message_ordinal=ordinal,
            ))
            continue
        for local_index, part in enumerate(parts):
            part_index = int(indices[local_index]) if indices is not None else local_index
            kind = str(part.get("part_kind") or "")
            call_id = part.get("tool_call_id")
            part_key = f"{kind}:{call_id}" if call_id else f"{kind}:{part_index}"
            piece = {**serialized, "parts": [part]}
            event_type = event_type_for_message(piece)
            if has_results and kind == "user-prompt":
                event_type = "model/request"
            drafts.append(MessageEventDraft(
                event_id=uuid5(NAMESPACE_URL,
                    f"deep-agent-part:{session_id}:{run_id}:{ordinal}:{part_key}").hex,
                event_type=event_type,
                search_text=build_search_text(event_type, piece),
                payload={"message": piece, "source": {
                    "message_ordinal": ordinal, "part_index": part_index, "part_key": part_key,
                }},
            ))
    return drafts


def group_message_events(events: Sequence[Any]) -> list[Any]:
    """Reassemble SDK boundaries for model/history reads; facts remain separate."""
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    result: list[Any] = []
    for event in sorted(events, key=lambda item: int(_get(item, "sequence"))):
        payload = _get_optional(event, "payload") or {}
        source = payload.get("source") if isinstance(payload, Mapping) else None
        if _get(event, "event_type") not in MESSAGE_EVENT_TYPES or not isinstance(source, Mapping):
            result.append(event)
            continue
        key = (_get_optional(event, "session_id"), _get_optional(event, "run_id"),
               source["message_ordinal"])
        target = grouped.get(key)
        if target is None:
            target = {field: _get_optional(event, field) for field in (
                "event_id", "session_id", "sequence", "schema_version", "event_type",
                "status", "turn_id", "run_id", "created_at", "updated_at",
            )}
            target["payload"] = deepcopy(payload)
            target["payload"]["message"]["parts"] = []
            target["source_event_ids"] = []
            target["_parts"] = []
            target["through_sequence"] = int(_get(event, "sequence"))
            grouped[key] = target
            result.append(target)
        message = payload["message"]
        target["_parts"].extend((int(source["part_index"]), part)
                                for part in message.get("parts", []))
        target["source_event_ids"].append(str(_get(event, "event_id")))
        target["through_sequence"] = max(target["through_sequence"], int(_get(event, "sequence")))
        target["updated_at"] = max(target["updated_at"], _get(event, "updated_at"))
        if _get_optional(event, "status") is not None:
            target["status"] = _get(event, "status")
        # All immutable parts share the same SDK header; a terminal commit may
        # finalize usage/state without replacing any already persisted content.
        target["payload"]["message"].update({k: deepcopy(v) for k, v in message.items() if k != "parts"})
    for target in grouped.values():
        target["payload"]["message"]["parts"] = [
            part for _, part in sorted(target.pop("_parts"), key=lambda entry: entry[0])
        ]
        target["event_type"] = event_type_for_message(target["payload"]["message"])
    return result


def decode_message_events(events: Sequence[Any]) -> list[ModelMessage]:
    messages: list[ModelMessage] = []
    for event in group_message_events(events):
        if str(_get(event, "event_type")) not in MESSAGE_EVENT_TYPES:
            continue
        schema_version = int(_get(event, "schema_version"))
        if schema_version != EVENT_SCHEMA_VERSION:
            raise EventCodecError(f"Unsupported session event schema_version={schema_version}")
        payload = _get(event, "payload")
        if not isinstance(payload, Mapping) or not isinstance(payload.get("message"), dict):
            raise EventCodecError("Message Event is missing payload.message")
        try:
            messages.append(deserialize_message(payload["message"]))
        except Exception as exc:
            raise EventCodecError(
                f"Message Event {_get_optional(event, 'event_id') or 'unknown'} cannot be restored"
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
        isinstance(part, Mapping) and part.get("part_kind") in _TOOL_RESULT_PART_KINDS
        for part in part_items
    ):
        return "tool/results"
    if any(
        isinstance(part, Mapping) and part.get("part_kind") == "user-prompt"
        for part in part_items
    ):
        return "user/message"
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
    return _truncate_utf8(normalized, MAX_SEARCH_TEXT_BYTES) or None


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


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
