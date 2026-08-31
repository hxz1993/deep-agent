from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pydantic_ai import ToolDenied
from pydantic_ai.messages import (
    BaseToolCallPart,
    BaseToolReturnPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolReturnPart,
    UserPromptPart,
)

from deep_agent.agent.context_references import (
    context_reference_title,
    context_references_from_content,
)
from deep_agent.time import ensure_utc


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


def is_thinking_only_response(response: ModelResponse) -> bool:
    """True when a model turn has thinking but no user-visible text or tool calls."""
    if not response.parts:
        return False
    if any(isinstance(part, TextPart) and part.content for part in response.parts):
        return False
    if any(isinstance(part, BaseToolCallPart) for part in response.parts):
        return False
    return all(isinstance(part, ThinkingPart) for part in response.parts)


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
            for part in message.parts:
                if isinstance(part, BaseToolReturnPart):
                    pop_call(part.tool_call_id, part.tool_name)
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
            elif isinstance(part, BaseToolReturnPart):
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
        if (
            isinstance(message, ModelResponse)
            and message.state != "interrupted"
            and is_thinking_only_response(message)
        ):
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
                if message.state != "interrupted" and is_thinking_only_response(message):
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


def mark_cancelled_response_interrupted(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Mark the final retained assistant response as interrupted after a real cancel.

    Pydantic AI can cancel while a function is executing and return a partial
    response whose state remains ``complete``.  DeepAgent's History contract is
    run-oriented, so any retained assistant content from that cancelled run must
    remain distinguishable from a normally completed answer.
    """
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, ModelResponse) or not message.parts:
            continue
        if message.state == "interrupted":
            return messages
        updated = list(messages)
        updated[index] = replace(message, state="interrupted")
        return updated
    return messages


_USER_CANCELLED_TOOL_MESSAGE = "User cancelled"


def repair_unprocessed_tool_calls(
    messages: list[ModelMessage],
    *,
    denied_message: str = _USER_CANCELLED_TOOL_MESSAGE,
) -> list[ModelMessage]:
    """Close legacy dangling tool calls without touching official interrupted turns.

    Synthetic tool returns are inserted immediately after the assistant turn they
    belong to, not only at history tail. Pydantic AI owns the repair of
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
                    content=ToolDenied(message=denied_message),
                    tool_call_id=tool_call_id,
                )
                for tool_call_id, tool_name in pending.items()
            ]
        ))
        pending = {}

    for message in messages:
        if isinstance(message, ModelResponse):
            returned_ids = {
                part.tool_call_id
                for part in message.parts
                if isinstance(part, BaseToolReturnPart) and part.tool_call_id
            }
            for tool_call_id in returned_ids:
                pending.pop(tool_call_id, None)
            flush_pending()
            repaired.append(message)
            if message.state == "interrupted":
                continue
            for part in message.parts:
                if isinstance(part, BaseToolCallPart) and part.tool_call_id:
                    pending[part.tool_call_id] = part.tool_name
            for tool_call_id in returned_ids:
                pending.pop(tool_call_id, None)
            continue

        if isinstance(message, ModelRequest):
            if pending and any(isinstance(part, UserPromptPart) for part in message.parts):
                flush_pending()
            repaired.append(message)
            for part in message.parts:
                if isinstance(part, BaseToolReturnPart) and part.tool_call_id:
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
            for part in message.parts:
                if isinstance(part, BaseToolReturnPart) and part.tool_call_id:
                    pending.discard(part.tool_call_id)
        elif isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, (BaseToolReturnPart, RetryPromptPart)) and part.tool_call_id:
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


def _request_timestamp(request: ModelRequest) -> datetime | None:
    if request.timestamp is not None:
        return request.timestamp
    for part in request.parts:
        if isinstance(part, UserPromptPart) and part.timestamp is not None:
            return part.timestamp
    return None


def first_user_prompt(messages: list[ModelMessage]) -> str | None:
    """Return the first user message text from raw model history."""
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, UserPromptPart):
                continue
            text = _user_prompt_text(part).strip()
            if text:
                return text
            content = part.content if isinstance(part.content, list) else []
            attachments = [
                item
                for item in content
                if isinstance(item, dict)
                and item.get("kind") == "attachment-ref"
                and item.get("id")
            ]
            if attachments:
                names = [str(item.get("name") or item["id"]) for item in attachments]
                return "[附件] " + ", ".join(names)
            context_refs = context_references_from_content(part.content)
            if context_refs:
                return context_reference_title(context_refs)
    return None


def session_activity_timestamps(messages: list[ModelMessage]) -> tuple[datetime | None, datetime | None]:
    """Infer session created/updated times from message timestamps."""
    timestamps: list[datetime] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            ts = _request_timestamp(message)
            if ts is not None:
                timestamps.append(ensure_utc(ts))
        elif isinstance(message, ModelResponse) and message.timestamp is not None:
            timestamps.append(ensure_utc(message.timestamp))
    if not timestamps:
        return None, None
    return min(timestamps), max(timestamps)
