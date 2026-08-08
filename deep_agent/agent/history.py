from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import ToolDenied
from pydantic_ai.messages import (
    BaseToolCallPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolReturnPart,
    UserPromptPart,
    NativeToolSearchReturnPart,
)

from deep_agent.agent.attachments import is_attachment_ref
from deep_agent.agent.context_references import (
    context_reference_title,
    context_references_from_content,
)
from deep_agent.agent.events import _to_jsonable
from deep_agent.schemas.context_reference import ContextReference


class HistoryTextPart(BaseModel):
    type: Literal["text"] = "text"
    content: str


class HistoryThinkingPart(BaseModel):
    type: Literal["thinking"] = "thinking"
    content: str


class HistoryToolCallPart(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    tool: str
    args: Any = None
    tool_call_id: str | None = None
    widget: str | None = None
    metadata: dict[str, Any] | None = None
    source: str | None = None


class HistoryToolResultPart(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool: str | None = None
    result: Any = None
    tool_call_id: str | None = None
    source: str | None = None


class HistoryAttachmentPart(BaseModel):
    type: Literal["attachment"] = "attachment"
    id: str
    name: str | None = None
    mime: str | None = None
    extracted_text: str | None = None


HistoryPart = (
    HistoryTextPart
    | HistoryThinkingPart
    | HistoryToolCallPart
    | HistoryToolResultPart
    | HistoryAttachmentPart
)


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    status: Literal["completed", "interrupted"] | None = Field(
        default=None,
        description="Assistant response state; omitted for user turns and synthetic result groups.",
    )
    content: str | None = Field(default=None, description="User message text.")
    attachments: list[HistoryAttachmentPart] = Field(
        default_factory=list,
        description="User-uploaded files attached to this turn.",
    )
    context_refs: list[ContextReference] = Field(
        default_factory=list,
        description="Structured context snapshots attached to this user turn.",
    )
    parts: list[HistoryPart] = Field(default_factory=list, description="Assistant message parts in order.")
    timestamp: datetime | None = Field(default=None, description="Message time (UTC).")
    branch_point: int | None = Field(
        default=None,
        description="Index in raw ModelMessage list; truncate here to branch.",
    )


class HistoryContextCheckpoint(BaseModel):
    source_last_sequence: int
    covered_model_message_count: int
    source_estimated_tokens: int
    projected_estimated_tokens: int
    reduction_ratio: float
    new_message_count: int
    summary_model_id: str | None = None
    row_version: int
    updated_at: datetime


class SessionHistoryResponse(BaseModel):
    session_id: str
    messages: list[HistoryMessage]
    model_message_count: int = Field(
        default=0,
        description="Raw ModelMessage count; use as branch_from to append at end.",
    )
    context_checkpoint: HistoryContextCheckpoint | None = Field(
        default=None,
        description="Current active context checkpoint; not a chat message.",
    )


def _user_prompt_attachments(part: UserPromptPart) -> list[HistoryAttachmentPart]:
    content = part.content
    if isinstance(content, str):
        return []
    if not isinstance(content, list):
        return []
    attachments: list[HistoryAttachmentPart] = []
    for item in content:
        if not is_attachment_ref(item):
            continue
        attachments.append(
            HistoryAttachmentPart(
                id=str(item["id"]),
                name=item.get("name"),
                mime=item.get("mime"),
                extracted_text=item.get("extracted_text"),
            )
        )
    return attachments


def _user_prompt_context_refs(part: UserPromptPart) -> list[ContextReference]:
    return context_references_from_content(part.content)


def _user_prompt_text(part: UserPromptPart) -> str:
    content = part.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict) and item.get("kind") == "text-content":
                chunks.append(str(item.get("content", "")))
        return "\n".join(c for c in chunks if c)
    return str(content)


def _tool_call_args(part: BaseToolCallPart) -> Any:
    if not part.args:
        return None
    if isinstance(part.args, dict):
        return part.args
    try:
        return part.args_as_dict()
    except Exception:  # noqa: BLE001
        return part.args


def is_thinking_only_response(response: ModelResponse) -> bool:
    """True when a model turn has thinking but no user-visible text or tool calls."""
    if not response.parts:
        return False
    if any(isinstance(part, TextPart) and part.content for part in response.parts):
        return False
    if any(isinstance(part, BaseToolCallPart) for part in response.parts):
        return False
    return all(isinstance(part, ThinkingPart) for part in response.parts)


def is_retry_only_request(request: ModelRequest) -> bool:
    """True for pydantic_ai output-validation retry prompts (not user turns)."""
    if not request.parts:
        return False
    return all(isinstance(part, RetryPromptPart) for part in request.parts)


def is_user_only_request(message: ModelMessage) -> bool:
    """True for a user prompt turn (no tool returns mixed in)."""
    if not isinstance(message, ModelRequest):
        return False
    if not message.parts:
        return False
    return all(isinstance(part, UserPromptPart) for part in message.parts)


@dataclass(frozen=True)
class _RejectedToolCalls:
    rejected_positions: dict[int, set[int]]
    retry_positions: dict[int, set[int]]
    terminal_rejected_positions: dict[int, set[int]]
    terminal_retry_positions: dict[int, set[int]]


def _rejected_tool_calls(messages: list[ModelMessage]) -> _RejectedToolCalls:
    """Match retry prompts to preceding calls without relying on globally unique IDs."""
    unresolved: list[tuple[int, int, str, str | None]] = []
    rejected: dict[int, set[int]] = {}
    retries: dict[int, set[int]] = {}
    terminal_rejected: dict[int, set[int]] = {}
    terminal_retries: dict[int, set[int]] = {}
    last_response_index = max(
        (index for index, message in enumerate(messages) if isinstance(message, ModelResponse)),
        default=-1,
    )

    def pop_call(tool_call_id: str | None, tool_name: str | None = None) -> tuple[int, int] | None:
        for index in range(len(unresolved) - 1, -1, -1):
            message_index, part_index, candidate_name, candidate_id = unresolved[index]
            if candidate_id != tool_call_id:
                continue
            if tool_name and candidate_name != tool_name:
                continue
            unresolved.pop(index)
            return message_index, part_index
        return None

    for message_index, message in enumerate(messages):
        if isinstance(message, ModelResponse):
            for part_index, part in enumerate(message.parts):
                if isinstance(part, BaseToolCallPart):
                    unresolved.append(
                        (message_index, part_index, part.tool_name, part.tool_call_id)
                    )
            continue

        if not isinstance(message, ModelRequest):
            continue
        for part_index, part in enumerate(message.parts):
            if isinstance(part, RetryPromptPart):
                position = pop_call(part.tool_call_id, part.tool_name)
                if position is not None:
                    rejected.setdefault(position[0], set()).add(position[1])
                    retries.setdefault(message_index, set()).add(part_index)
                    if message_index >= last_response_index:
                        terminal_rejected.setdefault(position[0], set()).add(position[1])
                        terminal_retries.setdefault(message_index, set()).add(part_index)
            elif isinstance(part, ToolReturnPart):
                pop_call(part.tool_call_id, part.tool_name)

    return _RejectedToolCalls(
        rejected_positions=rejected,
        retry_positions=retries,
        terminal_rejected_positions=terminal_rejected,
        terminal_retry_positions=terminal_retries,
    )


def filter_ephemeral_agent_messages(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Drop thinking-only retries and validation prompts from persisted history."""
    rejected_calls = _rejected_tool_calls(messages)
    filtered: list[ModelMessage] = []
    for message_index, message in enumerate(messages):
        if isinstance(message, ModelResponse) and is_thinking_only_response(message):
            continue
        if isinstance(message, ModelResponse):
            rejected_parts = (
                rejected_calls.rejected_positions.get(message_index, set())
                - rejected_calls.terminal_rejected_positions.get(message_index, set())
            )
            kept_parts = [
                part
                for part_index, part in enumerate(message.parts)
                if part_index not in rejected_parts
            ]
            if not kept_parts:
                continue
            if len(kept_parts) != len(message.parts):
                message = replace(message, parts=kept_parts)
                if is_thinking_only_response(message):
                    continue
        if isinstance(message, ModelRequest):
            terminal_retries = rejected_calls.terminal_retry_positions.get(message_index, set())
            kept_parts = [
                part
                for part_index, part in enumerate(message.parts)
                if not isinstance(part, RetryPromptPart) or part_index in terminal_retries
            ]
            if not kept_parts:
                continue
            if len(kept_parts) != len(message.parts):
                message = replace(message, parts=kept_parts)
        filtered.append(message)
    return filtered


_USER_CANCELLED_TOOL_MESSAGE = "User cancelled"


def repair_unprocessed_tool_calls(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Close legacy dangling tool calls without touching official interrupted turns.

    Synthetic tool returns are inserted immediately after the assistant turn they
    belong to, not only at history tail. Pydantic AI 2.26 owns the repair of
    ``state='interrupted'`` responses and synthesizes ``outcome='interrupted'``
    when that history is reused, so those responses must remain unchanged.
    """
    if not messages:
        return messages

    repaired: list[ModelMessage] = []
    pending: dict[str, str] = {}

    def flush_pending() -> None:
        nonlocal pending
        if not pending:
            return
        repaired.append(ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name=tool_name,
                    content=ToolDenied(message=_USER_CANCELLED_TOOL_MESSAGE),
                    tool_call_id=tool_call_id,
                )
                for tool_call_id, tool_name in pending.items()
            ]
        ))
        pending = {}

    for message in messages:
        if isinstance(message, ModelResponse):
            flush_pending()
            repaired.append(message)
            if message.state == "interrupted":
                continue
            for part in message.parts:
                if isinstance(part, BaseToolCallPart) and part.tool_call_id:
                    pending[part.tool_call_id] = part.tool_name
            continue

        if isinstance(message, ModelRequest):
            if pending and any(isinstance(part, UserPromptPart) for part in message.parts):
                flush_pending()
            repaired.append(message)
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and part.tool_call_id:
                    pending.pop(part.tool_call_id, None)
            continue

        flush_pending()
        repaired.append(message)

    flush_pending()
    if len(repaired) == len(messages):
        return messages
    return repaired


def has_unprocessed_tool_calls(messages: list[ModelMessage]) -> bool:
    """Return whether persisted history is waiting for a tool/deferred result."""

    pending: set[str] = set()
    for message in messages:
        if isinstance(message, ModelResponse):
            if message.state == "interrupted":
                continue
            for part in message.parts:
                if isinstance(part, BaseToolCallPart) and part.tool_call_id:
                    pending.add(part.tool_call_id)
        elif isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, (ToolReturnPart, RetryPromptPart)) and part.tool_call_id:
                    pending.discard(part.tool_call_id)
    return bool(pending)


def incremental_append_messages(
    messages: list[ModelMessage],
    *,
    skip_user_prompt: bool,
) -> list[ModelMessage]:
    """Messages to persist after a run when the user prompt was pre-appended."""
    filtered = filter_ephemeral_agent_messages(messages)
    if not skip_user_prompt:
        return filtered
    return [message for message in filtered if not is_user_only_request(message)]


def _response_parts(
    response: ModelResponse,
    *,
    rejected_part_indices: set[int] | None = None,
) -> list[HistoryPart]:
    parts: list[HistoryPart] = []
    rejected_part_indices = rejected_part_indices or set()
    for part_index, part in enumerate(response.parts):
        if part_index in rejected_part_indices:
            continue
        if isinstance(part, TextPart) and part.content:
            parts.append(HistoryTextPart(content=part.content))
        elif isinstance(part, ThinkingPart) and part.content:
            parts.append(HistoryThinkingPart(content=part.content))
        elif isinstance(part, BaseToolCallPart):
            parts.append(
                HistoryToolCallPart(
                    tool=part.tool_name,
                    args=_tool_call_args(part),
                    tool_call_id=part.tool_call_id,
                    source=(
                        "tool_search"
                        if part.tool_name in {"search_tools", "tool_search"}
                        else None
                    ),
                )
            )
        elif isinstance(part, NativeToolSearchReturnPart):
            parts.append(
                HistoryToolResultPart(
                    tool=part.tool_name,
                    result=_to_jsonable(part.content),
                    tool_call_id=part.tool_call_id,
                    source="tool_search",
                )
            )
    return parts


def _request_tool_returns(request: ModelRequest) -> list[HistoryPart]:
    parts: list[HistoryPart] = []
    for part in request.parts:
        if isinstance(part, ToolReturnPart):
            parts.append(
                HistoryToolResultPart(
                    tool=part.tool_name,
                    result=_to_jsonable(part.content),
                    tool_call_id=part.tool_call_id,
                    source=(
                        "tool_search"
                        if part.tool_name in {"search_tools", "tool_search"}
                        else None
                    ),
                )
            )
    return parts


def _request_timestamp(request: ModelRequest) -> datetime | None:
    if request.timestamp is not None:
        return request.timestamp
    for part in request.parts:
        if isinstance(part, UserPromptPart) and part.timestamp is not None:
            return part.timestamp
    return None


def first_user_prompt(messages: list[ModelMessage]) -> str | None:
    """Return the first user message text from raw model history."""
    for item in model_messages_to_history(messages):
        if item.role == "user":
            if item.content:
                return item.content
            if item.attachments:
                names = [a.name or a.id for a in item.attachments]
                return "[附件] " + ", ".join(names)
            if item.context_refs:
                return context_reference_title(item.context_refs)
    return None


def _extract_assistant_text(messages: list[ModelMessage]) -> str:
    """从消息列表中提取助手响应文本。

    使用提前返回模式（if not ... continue）减少缩进深度。
    只处理 ModelResponse 中的 TextPart 内容。
    """
    texts: list[str] = []
    for msg in messages:
        # 只处理助手响应消息
        if not isinstance(msg, ModelResponse):
            continue
        for part in msg.parts:
            # 只处理文本片段
            if not isinstance(part, TextPart):
                continue
            # 跳过空内容
            if not part.content:
                continue
            texts.append(part.content)
    return "\n".join(texts).strip()


def session_activity_timestamps(messages: list[ModelMessage]) -> tuple[datetime | None, datetime | None]:
    """Infer session created/updated times from message timestamps."""
    timestamps: list[datetime] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            ts = _request_timestamp(message)
            if ts is not None:
                timestamps.append(ts)
        elif isinstance(message, ModelResponse) and message.timestamp is not None:
            timestamps.append(message.timestamp)
    if not timestamps:
        return None, None
    return min(timestamps), max(timestamps)


def model_messages_to_history(messages: list[ModelMessage]) -> list[HistoryMessage]:
    """Convert Pydantic AI message history to a frontend-friendly timeline."""
    rejected_calls = _rejected_tool_calls(messages)
    history: list[HistoryMessage] = []
    pending_tool_results: list[HistoryPart] = []
    pending_tool_results_timestamp: datetime | None = None
    pending_tool_results_branch_point: int | None = None

    def flush_tool_results() -> None:
        nonlocal pending_tool_results, pending_tool_results_timestamp, pending_tool_results_branch_point
        if not pending_tool_results:
            return
        if history and history[-1].role == "assistant":
            history[-1].parts.extend(pending_tool_results)
        else:
            history.append(
                HistoryMessage(
                    role="assistant",
                    parts=list(pending_tool_results),
                    timestamp=pending_tool_results_timestamp,
                    branch_point=pending_tool_results_branch_point,
                )
            )
        pending_tool_results = []
        pending_tool_results_timestamp = None
        pending_tool_results_branch_point = None

    for idx, message in enumerate(messages):
        if isinstance(message, ModelRequest):
            user_attachments: list[HistoryAttachmentPart] = []
            user_context_refs: list[ContextReference] = []
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    user_attachments.extend(_user_prompt_attachments(part))
                    user_context_refs.extend(_user_prompt_context_refs(part))
            user_texts = [_user_prompt_text(p) for p in message.parts if isinstance(p, UserPromptPart)]
            user_text = "\n".join(t for t in user_texts if t).strip() or None
            tool_results = _request_tool_returns(message)

            if user_text or user_attachments or user_context_refs:
                flush_tool_results()
                history.append(
                    HistoryMessage(
                        role="user",
                        content=user_text,
                        attachments=user_attachments,
                        context_refs=user_context_refs,
                        timestamp=_request_timestamp(message),
                        branch_point=idx,
                    )
                )
            if tool_results:
                if not pending_tool_results:
                    pending_tool_results_timestamp = _request_timestamp(message)
                    pending_tool_results_branch_point = idx
                pending_tool_results.extend(tool_results)

        elif isinstance(message, ModelResponse):
            if is_thinking_only_response(message):
                continue
            flush_tool_results()
            parts = _response_parts(
                message,
                rejected_part_indices=rejected_calls.rejected_positions.get(idx),
            )
            if parts:
                history.append(
                    HistoryMessage(
                        role="assistant",
                        status=(
                            "interrupted"
                            if message.state == "interrupted"
                            else "completed"
                        ),
                        parts=parts,
                        timestamp=message.timestamp,
                        branch_point=idx,
                    )
                )

    flush_tool_results()
    return history


def enrich_history_messages(
    messages: list[HistoryMessage],
    hints: dict[str, dict[str, Any]],
) -> list[HistoryMessage]:
    """Enrich UI calls and project cancelled tool execution for history display.

    Pydantic AI keeps a response ``completed`` when the model finished choosing
    a function tool and cancellation happened while that tool was executing.
    It adds the official ``outcome='interrupted'`` return when history is reused,
    but before the next run the Console still needs to distinguish that dangling
    function call from a completed one. Persisted model messages are not changed.

    Deferred UI tools are left pending because their missing result means
    "waiting for user input", not "run was stopped".
    """
    enriched: list[HistoryMessage] = []
    for msg in messages:
        if msg.role != "assistant":
            enriched.append(msg)
            continue
        parts: list[HistoryPart] = []
        for part in msg.parts:
            if isinstance(part, HistoryToolCallPart):
                extra = hints.get(part.tool, {})
                parts.append(part.model_copy(update=extra) if extra else part)
            else:
                parts.append(part)
        enriched.append(msg.model_copy(update={"parts": parts}))

    unresolved: list[tuple[str, int, str]] = []
    for message_index, msg in enumerate(enriched):
        if msg.role != "assistant":
            continue
        for part in msg.parts:
            if isinstance(part, HistoryToolCallPart) and part.tool_call_id:
                unresolved.append((part.tool_call_id, message_index, part.tool))
            elif isinstance(part, HistoryToolResultPart) and part.tool_call_id:
                for index in range(len(unresolved) - 1, -1, -1):
                    if unresolved[index][0] == part.tool_call_id:
                        unresolved.pop(index)
                        break

    interrupted_indices = {
        message_index
        for _, message_index, tool_name in unresolved
        if tool_name not in hints
    }
    return [
        msg.model_copy(update={"status": "interrupted"})
        if index in interrupted_indices and msg.status == "completed"
        else msg
        for index, msg in enumerate(enriched)
    ]
