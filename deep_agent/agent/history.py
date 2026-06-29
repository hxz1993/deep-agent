from __future__ import annotations

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
)

from deep_agent.agent.attachments import is_attachment_ref
from deep_agent.agent.events import _to_jsonable


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
    mcp_server_name: str | None = None


class HistoryToolResultPart(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool: str | None = None
    result: Any = None
    tool_call_id: str | None = None


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
    content: str | None = Field(default=None, description="User message text.")
    attachments: list[HistoryAttachmentPart] = Field(
        default_factory=list,
        description="User-uploaded files attached to this turn.",
    )
    parts: list[HistoryPart] = Field(default_factory=list, description="Assistant message parts in order.")
    timestamp: datetime | None = Field(default=None, description="Message time (UTC).")
    branch_point: int | None = Field(
        default=None,
        description="Index in raw ModelMessage list; truncate here to branch.",
    )


class SessionHistoryResponse(BaseModel):
    session_id: str
    messages: list[HistoryMessage]
    model_message_count: int = Field(
        default=0,
        description="Raw ModelMessage count; use as branch_from to append at end.",
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


def filter_ephemeral_agent_messages(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Drop thinking-only retries and validation prompts from persisted history."""
    filtered: list[ModelMessage] = []
    for message in messages:
        if isinstance(message, ModelResponse) and is_thinking_only_response(message):
            continue
        if isinstance(message, ModelRequest) and is_retry_only_request(message):
            continue
        filtered.append(message)
    return filtered


_USER_CANCELLED_TOOL_MESSAGE = "User cancelled"


def repair_unprocessed_tool_calls(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Close dangling tool_call turns so a new user message can run without pydantic_ai errors."""
    pending: dict[str, str] = {}
    for message in messages:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, BaseToolCallPart) and part.tool_call_id:
                    pending[part.tool_call_id] = part.tool_name
        elif isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and part.tool_call_id:
                    pending.pop(part.tool_call_id, None)
    if not pending:
        return messages
    denied = [
        ToolReturnPart(
            tool_name=tool_name,
            content=ToolDenied(message=_USER_CANCELLED_TOOL_MESSAGE),
            tool_call_id=tool_call_id,
        )
        for tool_call_id, tool_name in pending.items()
    ]
    return [*messages, ModelRequest(parts=denied)]


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


def _response_parts(response: ModelResponse) -> list[HistoryPart]:
    parts: list[HistoryPart] = []
    for part in response.parts:
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
    return None


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
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    user_attachments.extend(_user_prompt_attachments(part))
            user_texts = [_user_prompt_text(p) for p in message.parts if isinstance(p, UserPromptPart)]
            user_text = "\n".join(t for t in user_texts if t).strip() or None
            tool_results = _request_tool_returns(message)

            if user_text or user_attachments:
                flush_tool_results()
                history.append(
                    HistoryMessage(
                        role="user",
                        content=user_text,
                        attachments=user_attachments,
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
            parts = _response_parts(message)
            if parts:
                history.append(
                    HistoryMessage(
                        role="assistant",
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
    """Merge UI tool widget/metadata into history tool_call parts (API-layer only)."""
    if not hints:
        return messages
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
    return enriched
