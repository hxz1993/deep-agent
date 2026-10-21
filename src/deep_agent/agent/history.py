from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
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
    INTERRUPTED_TOOL_RETURN_CONTENT,
)

from deep_agent.agent.context_references import (
    context_reference_title,
    context_references_from_content,
)
from deep_agent.time import ensure_utc


def same_tool_return(left: ToolReturnPart, right: ToolReturnPart) -> bool:
    """Compare a settled result, excluding only SDK assembly timestamps."""
    return type(left) is type(right) and all(
        getattr(left, field.name) == getattr(right, field.name)
        for field in fields(left) if field.name != "timestamp"
    )


def deduplicate_adjacent_tool_returns(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Project legacy repeated results without rewriting stored message facts.

    A provider response or user prompt starts a new local scope: IDs can be
    reused there. Conflicting results remain visible and retain SDK rejection.
    """
    result: list[ModelMessage] = []
    seen: list[ToolReturnPart] = []
    changed = False
    for message in messages:
        if not isinstance(message, ModelRequest):
            seen.clear()
            result.append(message)
            continue
        parts = []
        for part in message.parts:
            if isinstance(part, UserPromptPart):
                seen.clear()
            if isinstance(part, ToolReturnPart):
                if any(same_tool_return(previous, part) for previous in seen):
                    changed = True
                    continue
                seen.append(part)
            parts.append(part)
        if len(parts) == len(message.parts):
            result.append(message)
        elif parts or message.instructions:
            result.append(replace(message, parts=parts))
    return result if changed else messages


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
        if (message.metadata or {}).get("deepagent_output") or (message.metadata or {}).get("deepagent_output_budget"):
            filtered.append(message)
            continue
        if (
            isinstance(message, ModelResponse)
            and message.state != "interrupted"
            and not (message.metadata or {}).get("deepagent_error")
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
                metadata = dict(message.metadata or {})
                if "deepagent_part_indices" in metadata:
                    metadata["deepagent_part_indices"] = [
                        metadata["deepagent_part_indices"][i]
                        for i in range(len(message.parts)) if i not in rejected_parts
                    ]
                message = replace(message, parts=kept_parts, metadata=metadata)
                if (message.state != "interrupted" and not (message.metadata or {}).get("deepagent_error")
                        and is_thinking_only_response(message)):
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
                metadata = dict(message.metadata or {})
                if "deepagent_part_indices" in metadata:
                    metadata["deepagent_part_indices"] = [
                        metadata["deepagent_part_indices"][i]
                        for i, part in enumerate(message.parts)
                        if not isinstance(part, RetryPromptPart) or i in terminal_retries
                    ]
                message = replace(message, parts=kept_parts, metadata=metadata)
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


def _is_tool_denied_content(value: object) -> bool:
    return isinstance(value, ToolDenied) or (
        isinstance(value, Mapping) and value.get("kind") == "tool-denied"
    )


def _results_after_user_message(messages: list[ModelMessage]) -> dict[int, set[tuple[str, str | None]]]:
    """Match within one assistant segment in linear time, never by global call ID.

    A deferred resume may persist its user input before its tool result. Both
    requests belong to the preceding call until the next assistant response.
    """
    matched: dict[int, set[tuple[str, str | None]]] = {}
    calls: set[tuple[str, str | None]] = set()
    response_index = -1
    crossed_user = False
    for index, message in enumerate(messages):
        if isinstance(message, ModelResponse):
            response_index = index
            crossed_user = False
            calls = {(p.tool_call_id, p.tool_name) for p in message.parts
                     if isinstance(p, BaseToolCallPart) and p.tool_call_id}
        elif isinstance(message, ModelRequest):
            if (any(isinstance(p, UserPromptPart) for p in message.parts)
                    and not any(isinstance(p, (BaseToolReturnPart, RetryPromptPart)) for p in message.parts)):
                crossed_user = True
            for part in message.parts:
                if isinstance(part, (BaseToolReturnPart, RetryPromptPart)) and part.tool_call_id:
                    key = (part.tool_call_id, part.tool_name)
                    if crossed_user and key in calls:
                        matched.setdefault(response_index, set()).add(key)
                    calls.discard(key)
        else:
            calls.clear()
            crossed_user = False
    return matched


def repair_unprocessed_tool_calls(
    messages: list[ModelMessage],
    *,
    denied_message: str = _USER_CANCELLED_TOOL_MESSAGE,
    failure_message: str | None = None,
    diagnostic_fields: Mapping[str, str | None] | None = None,
    tool_return_metadata: Mapping[str, dict[str, Any]] | None = None,
) -> list[ModelMessage]:
    """Close legacy dangling tool calls without touching official interrupted turns.

    Synthetic tool returns are inserted immediately after the assistant turn they
    belong to, not only at history tail. Pydantic AI owns the repair of
    ``state='interrupted'`` responses and synthesizes ``outcome='interrupted'``
    when that history is reused, so those responses must remain unchanged.
    ``failure_message`` is reserved for an exceptional Run exit; cancellation
    keeps the existing ToolDenied semantics instead of becoming a tool failure.
    """
    if not messages:
        return messages

    late_results = _results_after_user_message(messages)

    def observe(event: str, tool_call_id: str, tool: str, reason: str) -> None:
        if diagnostic_fields is None:
            return
        try:
            from deep_agent.agent.observability import log_event
            log_event(event, **diagnostic_fields, tool_call_id=tool_call_id, tool=tool, reason=reason)
        except Exception:
            pass  # Diagnostics must never change history or persistence.

    repaired: list[ModelMessage] = []
    pending: dict[str, str] = {}
    retry_resolved: set[tuple[str, str | None]] = set()
    changed = False

    def flush_pending() -> None:
        nonlocal changed, pending
        if not pending:
            return
        preceding_metadata = repaired[-1].metadata or {} if repaired else {}
        source_metadata = ({"deepagent_message_ordinal": preceding_metadata["deepagent_message_ordinal"] + 1}
                           if "deepagent_message_ordinal" in preceding_metadata else None)
        repaired.append(ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name=tool_name,
                    content=(
                        failure_message
                        if failure_message is not None
                        else ToolDenied(message=denied_message)
                    ),
                    tool_call_id=tool_call_id,
                    outcome="failed" if failure_message is not None else "success",
                    metadata=(tool_return_metadata or {}).get(tool_call_id) if tool_name == "run_code" else None,
                )
                for tool_call_id, tool_name in pending.items()
            ], metadata=source_metadata,
        ))
        for call_id, tool_name in pending.items():
            observe("history_tool_result_synthesized", call_id, tool_name,
                    "missing_result_failed" if failure_message is not None else "missing_result_cancelled")
        changed = True
        pending = {}

    for message_index, message in enumerate(messages):
        if isinstance(message, ModelResponse):
            retry_resolved = set()
            returned_ids = {
                part.tool_call_id
                for part in message.parts
                if isinstance(part, BaseToolReturnPart) and part.tool_call_id
            }
            for tool_call_id in returned_ids:
                pending.pop(tool_call_id, None)
            flush_pending()
            repaired.append(message)
            if (message.metadata or {}).get("deepagent_dispatch", {}).get("rejected"):
                continue
            if message.state == "interrupted":
                # Pydantic AI will synthesize a return on the next run, but that
                # future-only receipt cannot carry diagnostics from this Run.
                # Persist one PTC-only interrupted receipt now, preserving the
                # SDK's outcome/content and all other interrupted tool behavior.
                ptc_parts = []
                for part in message.parts:
                    if not isinstance(part, BaseToolCallPart) or part.tool_name != "run_code" or not part.tool_call_id:
                        continue
                    metadata = (tool_return_metadata or {}).get(part.tool_call_id)
                    if metadata is None:
                        continue
                    later_return = False
                    for later in messages[message_index + 1:]:
                        if isinstance(later, ModelResponse):
                            break
                        if isinstance(later, ModelRequest) and any(
                            isinstance(item, BaseToolReturnPart) and item.tool_call_id == part.tool_call_id
                            for item in later.parts
                        ):
                            later_return = True
                            break
                    if not later_return:
                        ptc_parts.append(ToolReturnPart(
                            tool_name="run_code", content=INTERRUPTED_TOOL_RETURN_CONTENT,
                            tool_call_id=part.tool_call_id, outcome="interrupted", metadata=metadata,
                        ))
                if ptc_parts:
                    repaired.append(ModelRequest(parts=ptc_parts))
                    changed = True
                continue
            for part in message.parts:
                if isinstance(part, BaseToolCallPart) and part.tool_call_id:
                    if (part.tool_call_id, part.tool_name) in late_results.get(message_index, ()):
                        observe("history_tool_result_matched", part.tool_call_id, part.tool_name,
                                "result_after_user_message")
                    else:
                        pending[part.tool_call_id] = part.tool_name
            for tool_call_id in returned_ids:
                pending.pop(tool_call_id, None)
            continue

        if isinstance(message, ModelRequest):
            if (pending and any(isinstance(part, UserPromptPart) for part in message.parts)
                    and not any(isinstance(part, (BaseToolReturnPart, RetryPromptPart)) for part in message.parts)):
                flush_pending()
            kept_parts = []
            current_retry_resolved: set[tuple[str, str | None]] = set()
            for part in message.parts:
                if isinstance(part, RetryPromptPart) and part.tool_call_id:
                    pending.pop(part.tool_call_id, None)
                    current_retry_resolved.add((part.tool_call_id, part.tool_name))
                    kept_parts.append(part)
                    continue
                if isinstance(part, BaseToolReturnPart) and part.tool_call_id:
                    key = (part.tool_call_id, part.tool_name)
                    if key in retry_resolved and _is_tool_denied_content(
                        getattr(part, "content", None)
                    ):
                        changed = True
                        continue
                    pending.pop(part.tool_call_id, None)
                kept_parts.append(part)
            if kept_parts:
                if len(kept_parts) != len(message.parts):
                    message = replace(message, parts=kept_parts)
                repaired.append(message)
            else:
                changed = True
            retry_resolved = current_retry_resolved
            continue

        retry_resolved = set()
        flush_pending()
        repaired.append(message)

    flush_pending()
    if not changed:
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
