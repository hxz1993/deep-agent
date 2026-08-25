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
    {
        "model/system_prompt",
        "user/message",
        "user/speech",
        "assistant/text",
        "assistant/reasoning",
        "assistant/file",
        "assistant/speech",
        "tool/call",
        "tool/result",
        "tool/search_call",
        "tool/search_result",
        "capability/load_call",
        "capability/load_result",
        "tool/native_call",
        "tool/native_result",
        "model/retry",
        "model/tool_availability_delta",
        "provider/compaction_part",
        "model/unknown_part",
        "model/empty_message",
    }
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
    message_id: str
    part_index: int
    event_type: str
    call_id: str | None
    tool_name: str | None
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
    drafts: list[MessageEventDraft] = []
    for offset, message in enumerate(messages):
        serialized = serialize_message(message)
        parts = list(serialized.pop("parts", []))
        message_meta = serialized
        ordinal = first_message_ordinal + offset
        message_id = deterministic_message_id(
            session_id=session_id,
            run_id=run_id,
            message_ordinal=ordinal,
        )
        if not parts:
            drafts.append(
                MessageEventDraft(
                    message_id=message_id,
                    part_index=0,
                    event_type="model/empty_message",
                    call_id=None,
                    tool_name=None,
                    search_text=None,
                    payload={"message_meta": message_meta},
                )
            )
            continue
        for part_index, part in enumerate(parts):
            if not isinstance(part, dict):
                raise EventCodecError("Serialized Pydantic part must be an object")
            event_type = event_type_for_part(part, message_kind=message_meta.get("kind"))
            payload: dict[str, Any] = {"part": part}
            if part_index == 0:
                payload["message_meta"] = message_meta
            call_id = _string_or_none(part.get("tool_call_id"))
            tool_name = _string_or_none(part.get("tool_name"))
            drafts.append(
                MessageEventDraft(
                    message_id=message_id,
                    part_index=part_index,
                    event_type=event_type,
                    call_id=call_id,
                    tool_name=tool_name,
                    search_text=build_search_text(event_type, part),
                    payload=payload,
                )
            )
    return drafts


def decode_message_events(events: Sequence[Any]) -> list[ModelMessage]:
    messages: list[ModelMessage] = []
    current_message_id: str | None = None
    current_meta: dict[str, Any] | None = None
    current_parts: list[dict[str, Any]] = []
    expected_part_index = 0

    def flush() -> None:
        nonlocal current_message_id, current_meta, current_parts, expected_part_index
        if current_message_id is None:
            return
        if current_meta is None:
            raise EventCodecError(
                f"Message {current_message_id} has no message_meta on its first event"
            )
        try:
            messages.append(
                deserialize_message({**current_meta, "parts": list(current_parts)})
            )
        except Exception as exc:
            raise EventCodecError(
                f"Message {current_message_id} cannot be restored"
            ) from exc
        current_message_id = None
        current_meta = None
        current_parts = []
        expected_part_index = 0

    for event in sorted(events, key=lambda item: int(_get(item, "sequence"))):
        schema_version = int(_get(event, "schema_version"))
        if schema_version != EVENT_SCHEMA_VERSION:
            raise EventCodecError(
                f"Unsupported session event schema_version={schema_version}"
            )
        event_type = str(_get(event, "event_type"))
        if event_type not in MESSAGE_EVENT_TYPES:
            continue
        if event_type == "model/unknown_part":
            raise EventCodecError("Unknown Pydantic message part requires a DeepAgent upgrade")
        message_id = _string_or_none(_get(event, "message_id"))
        part_index_raw = _get(event, "part_index")
        if message_id is None or part_index_raw is None:
            raise EventCodecError("Message event is missing message_id or part_index")
        part_index = int(part_index_raw)
        if current_message_id != message_id:
            flush()
            current_message_id = message_id
        if part_index != expected_part_index:
            raise EventCodecError(
                f"Message {message_id} has non-contiguous part_index={part_index}, "
                f"expected={expected_part_index}"
            )
        payload = _get(event, "payload")
        if not isinstance(payload, dict):
            raise EventCodecError("Message event payload must be an object")
        if part_index == 0:
            meta = payload.get("message_meta")
            if not isinstance(meta, dict):
                raise EventCodecError("First message event is missing message_meta")
            current_meta = meta
        if event_type != "model/empty_message":
            part = payload.get("part")
            if not isinstance(part, dict):
                raise EventCodecError("Message event is missing its Pydantic part")
            current_parts.append(part)
        expected_part_index += 1
    flush()
    return messages


def deterministic_message_id(
    *,
    session_id: str,
    run_id: str | None,
    message_ordinal: int,
) -> str:
    return uuid5(
        NAMESPACE_URL,
        f"deep-agent:{session_id}:{run_id or 'unscoped'}:{message_ordinal}",
    ).hex


def event_type_for_part(part: Mapping[str, Any], *, message_kind: Any) -> str:
    part_kind = part.get("part_kind")
    tool_kind = part.get("tool_kind")
    if part_kind == "system-prompt":
        return "model/system_prompt"
    if part_kind == "instruction":
        return "model/system_prompt"
    if part_kind == "user-prompt":
        return "user/message"
    if part_kind == "speech":
        return "user/speech" if part.get("speaker") == "user" else "assistant/speech"
    if part_kind == "text":
        return "assistant/text"
    if part_kind == "thinking":
        return "assistant/reasoning"
    if part_kind == "file":
        return "assistant/file"
    if part_kind == "compaction":
        return "provider/compaction_part"
    if part_kind == "tool-call":
        if tool_kind == "tool-search":
            return "tool/search_call"
        if tool_kind == "capability-load":
            return "capability/load_call"
        return "tool/call"
    if part_kind == "tool-return":
        if tool_kind == "tool-search":
            return "tool/search_result"
        if tool_kind == "capability-load":
            return "capability/load_result"
        return "tool/result"
    if part_kind == "builtin-tool-call" and tool_kind == "tool-search":
        return "tool/search_call"
    if part_kind == "builtin-tool-return" and tool_kind == "tool-search":
        return "tool/search_result"
    if part_kind == "builtin-tool-call":
        return "tool/native_call"
    if part_kind == "builtin-tool-return":
        return "tool/native_result"
    if part_kind == "retry-prompt":
        return "model/retry"
    if part_kind == "tool-availability-delta":
        return "model/tool_availability_delta"
    return "model/unknown_part"


def build_search_text(event_type: str, part: Mapping[str, Any]) -> str | None:
    if event_type == "assistant/reasoning":
        return None
    safe = _search_projection(part)
    if safe in (None, "", [], {}):
        return None
    normalized = normalize_search_text(
        json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    return normalized[:MAX_SEARCH_TEXT_CHARS] or None


def normalize_search_text(value: str) -> str:
    return _WHITESPACE_RE.sub(
        " ",
        unicodedata.normalize("NFKC", value).casefold(),
    ).strip()


def _search_projection(value: Any, *, key: str | None = None) -> Any:
    if key is not None and key.casefold() in _SENSITIVE_SEARCH_KEYS:
        return None
    if isinstance(value, Mapping):
        projected = {
            str(child_key): child_value
            for child_key, child in value.items()
            if (child_value := _search_projection(child, key=str(child_key)))
            not in (None, "", [], {})
        }
        return projected
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


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value)
    return result if result else None
