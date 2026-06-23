from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
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


class HistoryToolResultPart(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool: str | None = None
    result: Any = None
    tool_call_id: str | None = None


HistoryPart = HistoryTextPart | HistoryThinkingPart | HistoryToolCallPart | HistoryToolResultPart


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str | None = Field(default=None, description="User message text.")
    parts: list[HistoryPart] = Field(default_factory=list, description="Assistant message parts in order.")
    timestamp: datetime | None = Field(default=None, description="Message time (UTC).")


class SessionHistoryResponse(BaseModel):
    session_id: str
    messages: list[HistoryMessage]


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
        if item.role == "user" and item.content:
            return item.content
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

    def flush_tool_results() -> None:
        nonlocal pending_tool_results, pending_tool_results_timestamp
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
                )
            )
        pending_tool_results = []
        pending_tool_results_timestamp = None

    for message in messages:
        if isinstance(message, ModelRequest):
            user_texts = [_user_prompt_text(p) for p in message.parts if isinstance(p, UserPromptPart)]
            user_text = "\n".join(t for t in user_texts if t).strip()
            tool_results = _request_tool_returns(message)

            if user_text:
                flush_tool_results()
                history.append(
                    HistoryMessage(role="user", content=user_text, timestamp=_request_timestamp(message))
                )
            if tool_results:
                if not pending_tool_results:
                    pending_tool_results_timestamp = _request_timestamp(message)
                pending_tool_results.extend(tool_results)

        elif isinstance(message, ModelResponse):
            if is_thinking_only_response(message):
                continue
            flush_tool_results()
            parts = _response_parts(message)
            if parts:
                history.append(HistoryMessage(role="assistant", parts=parts, timestamp=message.timestamp))

    flush_tool_results()
    return history
