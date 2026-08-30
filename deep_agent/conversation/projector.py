from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from deep_agent.agent.planning import is_planning_tool
from deep_agent.conversation.models import (
    AssistantConversationItem,
    ConversationArtifact,
    ConversationAttachment,
    ConversationHistoryResponse,
    ConversationItem,
    ConversationOrder,
    PlanConversationItem,
    ToolConversationItem,
    UserConversationItem,
)
from deep_agent.db.event_codec import EVENT_SCHEMA_VERSION, MESSAGE_EVENT_TYPES


_TOOL_CALL_KINDS = frozenset({"tool-call", "builtin-tool-call"})
_TOOL_RESULT_KINDS = frozenset(
    {"tool-return", "builtin-tool-return", "retry-prompt"}
)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class ConversationProjectionError(ValueError):
    pass


def project_session_events(
    events: Sequence[Any],
    *,
    session_id: str | None = None,
) -> ConversationHistoryResponse:
    """Project the append-only Event Store into the public chat rendering model."""

    ordered = sorted(events, key=lambda event: int(_get(event, "sequence", 0)))
    resolved_session_id = session_id or (
        str(_get(ordered[0], "session_id", "")) if ordered else ""
    )
    items: list[ConversationItem] = []
    tool_by_call: dict[str, ToolConversationItem | PlanConversationItem] = {}
    terminal_by_run: dict[str, str] = {}

    for event in ordered:
        event_type = str(_get(event, "event_type", ""))
        if event_type == "run/terminal":
            run_id = _optional_str(_get(event, "run_id"))
            if run_id:
                terminal_by_run[run_id] = _terminal_status(_get(event, "status"))
            continue
        if event_type not in MESSAGE_EVENT_TYPES:
            continue
        schema_version = int(_get(event, "schema_version", 0))
        if schema_version != EVENT_SCHEMA_VERSION:
            raise ConversationProjectionError(
                f"Unsupported message event schema_version={schema_version}"
            )
        message = _message(event)
        if message is None:
            continue
        if event_type == "user/message":
            item = _user_item(event, message)
            if item is not None:
                items.append(item)
            continue
        if event_type == "assistant/message":
            run_id = _optional_str(_get(event, "run_id") or message.get("run_id"))
            assistant_terminal = _assistant_terminal_status(event, message)
            if run_id and assistant_terminal:
                terminal_by_run[run_id] = assistant_terminal
            assistant = _assistant_item(event, message)
            if assistant is not None:
                items.append(assistant)
            for part_offset, part in enumerate(_parts(message)):
                part_kind = str(part.get("part_kind") or "")
                call_id = _optional_str(part.get("tool_call_id"))
                if not call_id:
                    continue
                if part_kind in _TOOL_CALL_KINDS:
                    tool = _tool_call_item(event, part, part_offset)
                    tool_by_call[call_id] = tool
                    items.append(tool)
                elif part_kind in _TOOL_RESULT_KINDS:
                    existing = tool_by_call.get(call_id)
                    if existing is None:
                        existing = _orphan_tool_result_item(event, part, part_offset)
                        tool_by_call[call_id] = existing
                        items.append(existing)
                    _apply_result(existing, event, part)
            continue
        if event_type in {"tool/results", "model/request"}:
            for part_offset, part in enumerate(_parts(message)):
                if str(part.get("part_kind") or "") not in _TOOL_RESULT_KINDS:
                    continue
                call_id = _optional_str(part.get("tool_call_id"))
                if not call_id:
                    continue
                existing = tool_by_call.get(call_id)
                if existing is None:
                    existing = _orphan_tool_result_item(event, part, part_offset)
                    tool_by_call[call_id] = existing
                    items.append(existing)
                _apply_result(existing, event, part)

    for item in items:
        if item.kind not in {"tool", "plan"} or item.status not in {
            "pending",
            "awaiting_input",
        }:
            continue
        terminal = terminal_by_run.get(item.run_id or "")
        if terminal:
            item.status = terminal  # type: ignore[assignment]

    _mark_final_assistant_items(items)

    items.sort(key=lambda item: (item.order.sequence, item.order.part_offset, item.item_id))
    return ConversationHistoryResponse(
        session_id=resolved_session_id,
        event_head_sequence=max(
            (int(_get(event, "sequence", 0)) for event in ordered),
            default=0,
        ),
        items=items,
    )


def _message(event: Any) -> dict[str, Any] | None:
    payload = _get(event, "payload")
    if not isinstance(payload, Mapping):
        return None
    message = payload.get("message")
    return dict(message) if isinstance(message, Mapping) else None


def _parts(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(part) for part in message.get("parts") or [] if isinstance(part, Mapping)]


def _user_item(event: Any, message: Mapping[str, Any]) -> UserConversationItem | None:
    texts: list[str] = []
    attachments: list[ConversationAttachment] = []
    context_refs: list[dict[str, Any]] = []
    part_offset = 0
    timestamp = _event_datetime(event, "created_at")
    found = False
    for offset, part in enumerate(_parts(message)):
        if part.get("part_kind") != "user-prompt":
            continue
        if not found:
            part_offset = offset
        found = True
        timestamp = _datetime(part.get("timestamp")) or timestamp
        content = part.get("content")
        if isinstance(content, str):
            if content:
                texts.append(content)
            continue
        if not isinstance(content, list):
            continue
        for value in content:
            if isinstance(value, str):
                if value:
                    texts.append(value)
                continue
            if not isinstance(value, Mapping):
                continue
            kind = value.get("kind")
            if kind == "text-content":
                text = str(value.get("content") or "")
                if text:
                    texts.append(text)
            elif kind == "attachment-ref" and value.get("id"):
                attachments.append(
                    ConversationAttachment(
                        id=str(value["id"]),
                        name=_optional_str(value.get("name")),
                        mime=_optional_str(value.get("mime") or value.get("mime_type")),
                        byte_size=_optional_int(value.get("byte_size")),
                        content_hash=_optional_str(value.get("content_hash")),
                    )
                )
            elif kind == "context-ref":
                reference = {
                    str(key): item
                    for key, item in value.items()
                    if key not in {"kind", "metadata"}
                }
                context_refs.append(reference)
    if not found:
        return None
    event_id = str(_get(event, "event_id"))
    return UserConversationItem(
        item_id=f"message:{event_id}",
        status="completed",
        turn_id=_optional_str(_get(event, "turn_id")),
        run_id=_optional_str(_get(event, "run_id") or message.get("run_id")),
        order=ConversationOrder(
            sequence=int(_get(event, "sequence", 0)),
            part_offset=part_offset,
        ),
        source_event_ids=[event_id],
        created_at=timestamp,
        updated_at=_event_datetime(event, "updated_at"),
        text="\n".join(texts).strip() or None,
        attachments=attachments,
        context_refs=context_refs,
    )


def _assistant_item(
    event: Any,
    message: Mapping[str, Any],
) -> AssistantConversationItem | None:
    thinking: list[str] = []
    texts: list[str] = []
    offsets: list[int] = []
    for offset, part in enumerate(_parts(message)):
        kind = part.get("part_kind")
        if kind == "thinking" and part.get("content"):
            thinking.append(str(part["content"]))
            offsets.append(offset)
        elif kind == "text" and part.get("content"):
            texts.append(str(part["content"]))
            offsets.append(offset)
    if not offsets:
        return None
    event_id = str(_get(event, "event_id"))
    state = str(message.get("state") or "")
    status = "interrupted" if state == "interrupted" else _assistant_status(_get(event, "status"))
    return AssistantConversationItem(
        item_id=f"message:{event_id}",
        status=status,
        turn_id=_optional_str(_get(event, "turn_id")),
        run_id=_optional_str(_get(event, "run_id") or message.get("run_id")),
        order=ConversationOrder(
            sequence=int(_get(event, "sequence", 0)),
            part_offset=min(offsets),
        ),
        source_event_ids=[event_id],
        created_at=_datetime(message.get("timestamp")) or _event_datetime(event, "created_at"),
        updated_at=_event_datetime(event, "updated_at"),
        thinking="\n".join(thinking).strip() or None,
        text="\n".join(texts).strip() or None,
        conversation_id=_optional_str(message.get("conversation_id")),
        model_name=_optional_str(message.get("model_name")),
        provider_name=_optional_str(message.get("provider_name")),
        provider_response_id=_optional_str(message.get("provider_response_id")),
        finish_reason=_optional_str(message.get("finish_reason")),
        usage=dict(message["usage"]) if isinstance(message.get("usage"), Mapping) else None,
    )


def _tool_call_item(
    event: Any,
    part: Mapping[str, Any],
    part_offset: int,
) -> ToolConversationItem | PlanConversationItem:
    event_id = str(_get(event, "event_id"))
    call_id = str(part["tool_call_id"])
    tool_name = _optional_str(part.get("tool_name"))
    pending_status = _pending_tool_status(event, part)
    common = dict(
        item_id=(f"plan:{call_id}" if is_planning_tool(tool_name) else f"tool:{call_id}"),
        status=pending_status,
        turn_id=_optional_str(_get(event, "turn_id")),
        run_id=_optional_str(_get(event, "run_id")),
        order=ConversationOrder(
            sequence=int(_get(event, "sequence", 0)),
            part_offset=part_offset,
        ),
        source_event_ids=[event_id],
        created_at=_event_datetime(event, "created_at"),
        updated_at=_event_datetime(event, "updated_at"),
        call_id=call_id,
        input=part.get("args"),
    )
    if is_planning_tool(tool_name):
        return PlanConversationItem(**common)
    metadata = dict(part["metadata"]) if isinstance(part.get("metadata"), Mapping) else None
    return ToolConversationItem(
        **common,
        tool_name=tool_name,
        mode=("deferred" if pending_status == "awaiting_input" else _tool_mode(tool_name, part)),
        widget=_optional_str((metadata or {}).get("widget")),
        metadata=metadata,
    )


def _orphan_tool_result_item(
    event: Any,
    part: Mapping[str, Any],
    part_offset: int,
) -> ToolConversationItem | PlanConversationItem:
    tool = _tool_call_item(
        event,
        {
            **part,
            "part_kind": "tool-call",
            "args": None,
        },
        part_offset,
    )
    tool.status = "pending"
    return tool


def _apply_result(
    item: ToolConversationItem | PlanConversationItem,
    event: Any,
    part: Mapping[str, Any],
) -> None:
    event_id = str(_get(event, "event_id"))
    if event_id not in item.source_event_ids:
        item.source_event_ids.append(event_id)
    item.updated_at = _event_datetime(event, "updated_at")
    item.output = part.get("content")
    outcome = _optional_str(part.get("outcome"))
    item.status = _result_status(part, outcome)
    if isinstance(item, PlanConversationItem):
        metadata = part.get("metadata")
        if isinstance(metadata, Mapping) and isinstance(metadata.get("deepagent_plan"), Mapping):
            item.plan = dict(metadata["deepagent_plan"])
            raw_items = item.plan.get("items")
            plan_items = raw_items if isinstance(raw_items, list) else []
            item.total = len(plan_items)
            item.completed = sum(
                isinstance(plan_item, Mapping)
                and plan_item.get("status") == "completed"
                for plan_item in plan_items
            )
        return
    item.outcome = outcome
    item.artifacts = _artifacts(part)
    metadata = part.get("metadata")
    if item.tool_name in {"web_search", "web_fetch"} and isinstance(metadata, Mapping):
        web_audit = metadata.get(item.tool_name)
        sources = metadata.get("sources")
        web_metadata = dict(item.metadata or {})
        if isinstance(web_audit, Mapping):
            web_metadata[item.tool_name] = dict(web_audit)
        if isinstance(sources, list):
            web_metadata["sources"] = sources
        item.metadata = web_metadata or None


def _artifacts(part: Mapping[str, Any]) -> list[ConversationArtifact]:
    metadata = part.get("metadata")
    if not isinstance(metadata, Mapping):
        return []
    artifacts: list[ConversationArtifact] = []
    value_handle = metadata.get("overflow_handle")
    if value_handle:
        artifacts.append(
            ConversationArtifact(
                artifact_id=str(value_handle),
                role="return_value",
                byte_size=_optional_int(metadata.get("overflow_bytes")),
                content_hash=_optional_str(metadata.get("overflow_hash")),
            )
        )
    content_handle = metadata.get("overflow_content_handle")
    if content_handle:
        artifacts.append(
            ConversationArtifact(
                artifact_id=str(content_handle),
                role="content",
                content_hash=_optional_str(metadata.get("overflow_content_hash")),
            )
        )
    return artifacts


def _pending_tool_status(event: Any, part: Mapping[str, Any]) -> str:
    metadata = part.get("metadata")
    widget = metadata.get("widget") if isinstance(metadata, Mapping) else None
    if _get(event, "status") == "pending" or widget:
        return "awaiting_input"
    return "pending"


def _result_status(part: Mapping[str, Any], outcome: str | None) -> str:
    kind = str(part.get("part_kind") or "")
    normalized = (outcome or "").casefold()
    if normalized in {"interrupted", "cancelled", "canceled"}:
        return "interrupted"
    if normalized in {"denied", "rejected"}:
        return "rejected"
    if normalized in {"failed", "failure", "error"}:
        return "failed"
    if kind == "retry-prompt" or normalized == "retry":
        return "removed"
    return "completed"


def _tool_mode(tool_name: str | None, part: Mapping[str, Any]) -> str:
    if tool_name in {"search_tools", "tool_search"}:
        return "tool_search"
    if part.get("part_kind") == "builtin-tool-call":
        return "native"
    metadata = part.get("metadata")
    if isinstance(metadata, Mapping) and metadata.get("widget"):
        return "deferred"
    return "tool"


def _assistant_status(value: Any) -> str:
    normalized = str(value or "completed").casefold()
    if normalized in {"pending", "interrupted", "failed"}:
        return normalized
    return "completed"


def _mark_final_assistant_items(items: Sequence[ConversationItem]) -> None:
    by_turn: dict[str, list[ConversationItem]] = {}
    for item in items:
        turn = item.turn_id or item.run_id or ""
        by_turn.setdefault(turn, []).append(item)
    for turn_items in by_turn.values():
        renderable = [
            item
            for item in turn_items
            if item.kind in {"tool", "plan"}
            or (
                isinstance(item, AssistantConversationItem)
                and bool(item.thinking or item.text)
            )
        ]
        if not renderable:
            continue
        last = max(
            renderable,
            key=lambda item: (item.order.sequence, item.order.part_offset, item.item_id),
        )
        if isinstance(last, AssistantConversationItem) and last.text:
            last.phase = "final"


def _terminal_status(value: Any) -> str:
    normalized = str(value or "interrupted").casefold()
    return normalized if normalized in {"failed", "interrupted"} else "interrupted"


def _assistant_terminal_status(
    event: Any,
    message: Mapping[str, Any],
) -> str | None:
    state = str(message.get("state") or "").casefold()
    status = str(_get(event, "status") or "").casefold()
    for value in (state, status):
        if value in {"interrupted", "failed"}:
            return value
    return None


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _event_datetime(event: Any, key: str) -> datetime:
    return _datetime(_get(event, key)) or _EPOCH
