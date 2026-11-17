"""Insert completed message facts at SDK boundaries; never persist live text snapshots."""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterable
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from typing import Any

from pydantic_ai import AgentStreamEvent, ModelRequestNode, RunContext, ToolDefinition, ToolDenied
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, ToolSearch
from pydantic_ai.exceptions import ToolFailedError
from pydantic_ai.messages import (
    FunctionToolCallEvent, FunctionToolResultEvent, ModelMessage, ModelRequest,
    ModelResponse, ToolCallPart, ToolReturn, ToolReturnPart, RetryPromptPart,
)
from pydantic_core import to_json

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_names import READ_ONLY_TOOL, public_tool_messages
from deep_agent.agent.history import is_user_only_request, same_tool_return
from deep_agent.agent.observability import log_event
from deep_agent.agent.planning import (
    attach_plan_snapshot, current_plan_snapshot, is_planning_tool, plan_snapshot_from_part,
)
from deep_agent.db.conversation import MessagePartWriteState, SqlConversationCoordinator
from deep_agent.db.run_ownership import live_run
from deep_agent.db.execution_error import safe_execution_error
from deep_agent.db.serialization import serialize_message
from deep_agent.diagnostics.persistence import persistence_failure_fields
from deep_agent.usage import response_call


class MessagePersistenceError(RuntimeError):
    """Further tools must not execute after a completed-message write failure."""


def _is_read_only_tool(tool_def: ToolDefinition | None) -> bool:
    return tool_def is not None and tool_def.kind == "function" and (
        tool_def.tool_kind in {"tool-search", "capability-load"}
        or (tool_def.metadata or {}).get(READ_ONLY_TOOL) is True
    )


class MessagePersistenceCapability(AbstractCapability[AgentDeps]):
    """Per-run instance, including provider retries; never share across Runs."""

    capture_execute_results = True
    _duplicate_flush_hooks: frozenset[str] = frozenset()

    def __init__(self, store: Any, *, history_length: int,
                 skip_user_prompt: bool, model_name: str | None = None,
                 provider_id: str | None = None,
                 event_queue: asyncio.Queue | None = None,
                 defer_read_only: bool = False) -> None:
        self.store = store
        self.event_queue = event_queue
        # Keep the original input length for callers; ledger ordering survives compaction.
        self.history_length = history_length
        self.skip_user_prompt = skip_user_prompt
        self.model_name = model_name
        self.provider_id = provider_id
        self.messages: list[ModelMessage] = []
        self.persistence_error: MessagePersistenceError | None = None
        self._ledger: list[ModelMessage] = []
        self._next_request: ModelRequest | None = None
        self._accepted: set[tuple[int, str]] = set()
        self._results: dict[str, Any] = {}
        self._write_lock = asyncio.Lock()
        self._buffer_read_only = defer_read_only and skip_user_prompt
        self._defer_writes = self._buffer_read_only
        self._write_options = {}
        if isinstance(store, SqlConversationCoordinator):
            self._write_options["write_state"] = MessagePartWriteState()
        self._committed_scope: tuple | None = None
        self._committed_public_messages: list[ModelMessage] = []
        self._committed_projection: bytes | None = None
        self._message_identity_merges = 0
        self._response_id_mismatches = 0
        self._executing_parent: ContextVar[tuple[int, str] | None] = ContextVar(
            "deepagent_message_executing_parent", default=None,
        )

    def get_ordering(self) -> CapabilityOrdering:
        # The public SDK orders after_* hooks in reverse. Outermost means our
        # after_tool_execute observes the existing limits/redaction's final
        # result, never the raw oversized result before those policies run.
        # ToolSearch is itself outermost and adds feedback to its tool return.
        # Resolve the tie explicitly so its final result is saved once, in both
        # ordinary and streaming runs, before immutable event comparison.
        return CapabilityOrdering(position="outermost", wraps=[ToolSearch])

    @staticmethod
    def _same_message(left: ModelMessage, right: ModelMessage) -> bool:
        if left is right:
            return True
        # Public terminal snapshots can replace a message to mark interruption.
        # Stable SDK metadata avoids conflating repeated text or tool IDs.
        def timestamp_of(message: ModelMessage):
            return getattr(message, "timestamp", None) or next(
                (part.timestamp for part in message.parts if getattr(part, "timestamp", None) is not None), None,
            )

        timestamp = timestamp_of(left)
        return (type(left) is type(right) and timestamp is not None
                and timestamp == timestamp_of(right)
                and getattr(left, "run_id", None) == getattr(right, "run_id", None))

    def _record(self, message: ModelMessage) -> None:
        for index, existing in enumerate(self._ledger):
            if self._same_message(existing, message):
                if existing is not message:
                    self._message_identity_merges += 1
                    old_id = getattr(existing, "provider_response_id", None)
                    new_id = getattr(message, "provider_response_id", None)
                    if isinstance(message, ModelResponse) and old_id and new_id and old_id != new_id:
                        self._response_id_mismatches += 1
                    self._accepted.update((id(message), call_id)
                                          for response_id, call_id in tuple(self._accepted)
                                          if response_id == id(existing))
                    self._ledger[index] = message
                return
        self._ledger.append(message)

    def canonical_messages(self, official: list[ModelMessage]) -> list[ModelMessage]:
        """Merge official terminal messages with pre-compaction node history.

        No partial preview/assembled return is added to successful history. The
        runner applies its usual terminal normalization and prompt filtering.
        """
        merged = list(self._ledger)
        # Settled parallel results may already be persisted while the SDK has
        # not entered the next model node (notably a deferred/UI boundary).
        # Reserve their existing ordinal and part positions before merging
        # terminal requests containing queued system/discovery notices.
        if self._results:
            tail = []
            for previous in reversed(merged):
                if not isinstance(previous, ModelRequest) or is_user_only_request(previous):
                    break
                tail.extend(previous.parts)
            remaining = [part for part in self._results.values()
                         if not isinstance(part, ToolReturnPart) or not any(
                             isinstance(existing, ToolReturnPart) and same_tool_return(existing, part)
                             for existing in tail)]
            if remaining:
                merged.append(ModelRequest(parts=deepcopy(remaining)))
        response_index: int | None = None
        for message in official:
            match = next((index for index, previous in enumerate(merged)
                          if self._same_message(previous, message)), None)
            if isinstance(message, ModelRequest) and not is_user_only_request(message):
                start = response_index + 1 if response_index is not None else 0
                candidates = []
                for index in range(start, len(merged)):
                    previous = merged[index]
                    if isinstance(previous, ModelResponse) or is_user_only_request(previous):
                        break
                    if isinstance(previous, ModelRequest):
                        candidates.append(index)
                if match is None:
                    # The SDK adds request headers at terminal assembly; the
                    # locally settled results identify this response's request.
                    match = next((index for index in candidates if any(
                        isinstance(left, ToolReturnPart) and isinstance(right, ToolReturnPart)
                        and same_tool_return(left, right)
                        for left in merged[index].parts for right in message.parts
                    )), None)
                    if match is None and response_index is not None and candidates:
                        match = candidates[0]
                if match is not None:
                    previous = merged[match]
                    parts = list(previous.parts)
                    for part in message.parts:
                        if any((same_tool_return(existing, part)
                                if isinstance(existing, ToolReturnPart) and isinstance(part, ToolReturnPart)
                                else existing == part) for existing in parts):
                            continue
                        parts.append(part)
                    # Keep committed parts first. Prepending a new system part
                    # would collide with an immutable result's part_index=0.
                    merged[match] = replace(message, parts=parts)
                    continue
            if match is None:
                merged.append(message)
                match = len(merged) - 1
            else:
                merged[match] = message
            if isinstance(message, ModelResponse):
                response_index = match
        return self._with_ordinals(merged)

    def _accept(self, ctx: RunContext[AgentDeps], call: ToolCallPart, *, allow_nested: bool = False) -> bool:
        # Providers can reuse IDs in later loops: acceptance is response-scoped.
        for message in reversed(ctx.messages):
            if isinstance(message, ModelResponse) and any(
                isinstance(part, ToolCallPart) and part.tool_call_id == call.tool_call_id
                and part.tool_name == call.tool_name for part in message.parts
            ):
                self._record(message)
                self._accepted.add((id(message), call.tool_call_id))
                return True
        # PTC dispatch uses normal SDK tool hooks for its authorized inner calls,
        # but those calls are not new model messages. Their durable outer tool is
        # already recorded; nested audit remains on its existing result metadata.
        # Context-local execution ancestry is stronger evidence than guessing a
        # parent from provider-controlled tool_call_id strings.
        parent = self._executing_parent.get()
        if allow_nested and parent is not None and parent in self._accepted:
            return False
        raise MessagePersistenceError("Accepted tool call has no official model response")

    def _copy_for_event_store(self, message: ModelMessage) -> ModelMessage:
        copy = deepcopy(message)
        if (
            self._buffer_read_only and isinstance(copy, ModelRequest) and copy.parts
            and all(isinstance(part, ToolReturnPart) for part in copy.parts)
        ):
            # A buffered result uses the same envelope as an immediately saved
            # result. The live SDK request retains its instructions and identity.
            return replace(copy, timestamp=None, instructions=None, run_id=None, conversation_id=None)
        return copy

    def _with_ordinals(self, messages: list[ModelMessage]) -> list[ModelMessage]:
        result = []
        ordinal = 1 if self.skip_user_prompt else 0
        for message in messages:
            if self.skip_user_prompt and is_user_only_request(message):
                continue
            copy = self._copy_for_event_store(message)
            copy.metadata = {**(copy.metadata or {}), "deepagent_message_ordinal": ordinal,
                             "deepagent_part_indices": list(range(len(copy.parts)))}
            ordinal += 1
            result.append(copy)
        return result

    def completed_messages(self) -> list[ModelMessage]:
        """Return accepted facts available at a checkpoint, even before they are committed."""
        messages = []
        ordinal = 1 if self.skip_user_prompt else 0
        accepted_in_round: set[str] = set()
        for message in self._ledger:
            if self.skip_user_prompt and is_user_only_request(message):
                continue
            copy = self._copy_for_event_store(message)
            indices = list(range(len(copy.parts)))
            if isinstance(message, ModelResponse):
                output_fact = bool((message.metadata or {}).get("deepagent_output"))
                accepted_in_round = {part.tool_call_id for part in message.parts
                                     if isinstance(part, ToolCallPart)
                                     and (id(message), part.tool_call_id) in self._accepted}
                indices = [i for i, part in enumerate(message.parts)
                           if output_fact or not isinstance(part, ToolCallPart)
                           or (id(message), part.tool_call_id) in self._accepted]
                copy.parts = [copy.parts[i] for i in indices]
            elif isinstance(message, ModelRequest):
                # Validation corrections were never accepted tool executions.
                # Do not persist their raw validators' arguments or exceptions.
                indices = [i for i, part in enumerate(copy.parts)
                           if (copy.metadata or {}).get("deepagent_output_budget") or not isinstance(part, RetryPromptPart)
                           or part.tool_call_id in accepted_in_round]
                copy.parts = [copy.parts[i] for i in indices]
                for part in copy.parts:
                    if isinstance(part, RetryPromptPart) and not (copy.metadata or {}).get("deepagent_output_budget"):
                        part.content = safe_execution_error({"message": part.content})["message"]
            copy.metadata = {**(copy.metadata or {}),
                             "deepagent_message_ordinal": ordinal,
                             "deepagent_part_indices": indices}
            ordinal += 1
            # Keep response headers for metering even when no accepted part
            # belongs in History (for example a rejected tool-call attempt).
            if copy.parts or isinstance(copy, ModelResponse):
                messages.append(copy)
        if self._results:
            # Supplemental media has no call ID; save it in the official SDK
            # request once ordering is fixed, not in a parallel-result draft.
            parts = deepcopy([part for part in self._results.values()
                              if not isinstance(part, RetryPromptPart)
                              or part.tool_call_id in accepted_in_round])
            for part in parts:
                if isinstance(part, RetryPromptPart):
                    part.content = safe_execution_error({"message": part.content})["message"]
            request = ModelRequest(parts=parts,
                                   metadata={"deepagent_message_ordinal": ordinal})
            if parts:
                messages.append(request)
        return messages

    def _projection(self, messages: list[ModelMessage]) -> bytes | None:
        digest = sha256()
        try:
            for message in messages:
                # The event codec uses this serializer, including part positions
                # and message ordinals. Usage tracks whether zero was reported.
                usage = (
                    response_call(message, request_id="", model_id=self.model_name,
                                  provider_id=self.provider_id, purpose="chat")
                    if isinstance(message, ModelResponse) else None
                )
                digest.update(to_json((serialize_message(message), usage)))
        except (TypeError, ValueError):
            return None
        return digest.digest()

    async def _flush(
        self, ctx: RunContext[AgentDeps], *, hook: str = "unknown",
        tool_call_id: str | None = None, phase: str | None = None,
    ) -> None:
        started_at = time.perf_counter()
        async with self._write_lock:
            if self.persistence_error is not None:
                raise self.persistence_error
            deps = ctx.deps
            locked_at = time.perf_counter()
            assembled_at = None
            transaction_ended_at = None
            committed_at = None
            outcome = "interrupted"
            messages = []
            write_stage = "assemble"
            try:
                messages = self.completed_messages()
                if hook == "_prepare_model_node" and self.skip_user_prompt and not messages:
                    # The runner already committed the user input before entering the SDK.
                    outcome = "skipped"
                    return
                scope = (deps.session_id, deps.agent_id, deps.user, deps.app_id,
                         deps.space_id, deps.turn_id, deps.run_id)
                messages_to_write = public_tool_messages(messages, deps.tool_names)
                projection = None
                if (
                    hook in self._duplicate_flush_hooks
                    and scope == self._committed_scope
                    and len(messages_to_write) == len(self._committed_public_messages)
                ):
                    projection = self._projection(messages_to_write)
                    if self._committed_projection is None:
                        self._committed_projection = self._projection(self._committed_public_messages)
                    if projection is not None and projection == self._committed_projection:
                        outcome = "skipped"
                        return
                write_stage = "append"
                try:
                    async with asyncio.timeout(10):
                        assembled_at = time.perf_counter()
                        rows = await self.store.append_message_parts(
                            deps.session_id, agent_id=deps.agent_id, user_id=deps.user,
                            app_id=deps.app_id, space_id=deps.space_id,
                            turn_id=deps.turn_id, run_id=deps.run_id,
                            messages=messages_to_write,
                            first_message_ordinal=1 if self.skip_user_prompt else 0,
                            usage_model_id=self.model_name,
                            usage_provider_id=self.provider_id,
                            **self._write_options,
                        )
                finally:
                    transaction_ended_at = time.perf_counter()
                committed_at = transaction_ended_at
                self.messages = messages
                self._committed_scope = scope
                self._committed_public_messages = messages_to_write
                self._committed_projection = projection
                write_stage = "publish"
                if self.event_queue is not None:
                    for row in rows or []:
                        self.event_queue.put_nowait({
                            "type": "session_event_appended", "persisted": True,
                            "event": row.public_dict(),
                        })
                outcome = "completed"
            except Exception as exc:
                outcome = "failed"
                log_event("message_persistence_failed", session_id=deps.session_id,
                          run_id=deps.run_id, agent_id=deps.agent_id,
                          phase="incremental", error_type=type(exc).__name__,
                          write_stage=write_stage,
                          message_identity_merges=self._message_identity_merges,
                          response_id_mismatches=self._response_id_mismatches,
                          **persistence_failure_fields(exc, store=self.store, messages=messages, hook=hook))
                self.persistence_error = MessagePersistenceError("Unable to persist completed message")
                raise self.persistence_error from exc
            finally:
                ended_at = time.perf_counter()
                log_event(
                    "message_persistence_timing", session_id=deps.session_id, run_id=deps.run_id,
                    agent_id=deps.agent_id, agent_name=getattr(deps, "agent_name", None),
                    request_index=getattr(ctx, "run_step", None), write_hook=hook, status=outcome,
                    tool_call_id=tool_call_id, phase=phase,
                    duration_ms=round((ended_at - started_at) * 1000),
                    lock_wait_ms=round((locked_at - started_at) * 1000),
                    assemble_ms=round(((assembled_at or ended_at) - locked_at) * 1000),
                    transaction_ms=(round((transaction_ended_at - assembled_at) * 1000)
                                    if transaction_ended_at is not None and assembled_at is not None else None),
                    publish_ms=round((ended_at - committed_at) * 1000) if committed_at is not None else None,
                )

    async def _save(
        self, ctx: RunContext[AgentDeps], *, hook: str = "unknown",
        tool_call_id: str | None = None, phase: str | None = None,
    ) -> None:
        if self.persistence_error is not None:
            raise self.persistence_error
        if not self._defer_writes:
            await self._flush(ctx, hook=hook, tool_call_id=tool_call_id, phase=phase)

    async def checkpoint_history(self, ctx: RunContext[AgentDeps]) -> None:
        """Make the source prefix visible to compaction's existing SQL validation."""
        if self._defer_writes:
            await self._flush(ctx, hook="context_compaction")

    async def wrap_node_run(self, ctx, *, node, handler):
        await self._prepare_model_node(ctx, node)
        return await handler(node)

    async def wrap_run(self, ctx, *, handler):
        # The SDK uses this hook for streaming and ordinary runs alike. Its
        # finally is guaranteed even when cancellation skips after_run hooks.
        with live_run(ctx.deps.run_id):
            return await handler()

    async def _prepare_model_node(self, ctx, node):
        if isinstance(node, ModelRequestNode):
            if ctx.deps.output_state is not None:
                ctx.deps.output_state.prepare_request(node.request)
            self._results.clear()
            if not node.is_resuming_without_prompt:
                # after_node may expose the next request before other public
                # before_node hooks replace it. It has not entered SDK history
                # yet, so replace that pending entry rather than duplicating it.
                if self._next_request is not None and self._next_request is not node.request:
                    self._ledger = [message for message in self._ledger if message is not self._next_request]
                self._record(node.request)
            self._next_request = None
            await self._save(ctx, hook="_prepare_model_node")

    async def after_model_request(self, ctx, *, request_context, response):
        if ctx.deps.output_state is not None:
            await ctx.deps.output_state.check_response(ctx, response, progress=self)
        if self._defer_writes:
            definitions = {tool.name: tool for tool in request_context.model_request_parameters.function_tools}
            if any(isinstance(part, ToolCallPart) and not _is_read_only_tool(definitions.get(part.tool_name))
                   for part in response.parts):
                # Keep the original timing of call facts for a batch that may write.
                self._defer_writes = False
        self._record(response)
        await self._save(ctx, hook="after_model_request")
        return response

    async def after_node_run(self, ctx, *, node, result):
        if isinstance(result, ModelRequestNode) and not result.is_resuming_without_prompt:
            self._record(result.request)
            self._next_request = result.request
            self._results.clear()
        # A CallToolsNode returned by UserPromptNode on deferred resume is the
        # previous Run's pending response, not a new message.
        if isinstance(node, ModelRequestNode):
            response = getattr(result, "model_response", None)
            if isinstance(response, ModelResponse):
                self._record(response)
        await self._save(ctx, hook="after_node_run")
        return result

    async def before_tool_execute(self, ctx, *, call, tool_def, args):
        self._accept(ctx, call, allow_nested=True)
        if not _is_read_only_tool(tool_def):
            # Unknown or side-effecting calls require the existing durable
            # dispatch guard and immediate capture of their settled outcomes.
            self._defer_writes = False
        await self._save(ctx, hook="before_tool_execute")
        return args

    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
        parent = previous_parent = self._executing_parent.get()
        for message in reversed(self._ledger):
            key = (id(message), call.tool_call_id)
            if key in self._accepted:
                parent = key
                break
        token = self._executing_parent.set(parent)
        try:
            return await handler(args)
        except ToolFailedError as exc:
            if self.capture_execute_results and previous_parent is None:
                self._results[call.tool_call_id] = deepcopy(exc.tool_failed)
                await self._save(ctx, hook="wrap_tool_execute")
            raise
        finally:
            self._executing_parent.reset(token)

    async def _capture_plan_result(self, ctx, part) -> bool:
        """Attach the actual returned plan before its immutable fact commits."""
        if (not isinstance(part, ToolReturnPart) or not is_planning_tool(part.tool_name)
                or part.outcome != "success"):
            return False
        snapshot = plan_snapshot_from_part(part)
        if snapshot is None:
            snapshot = await current_plan_snapshot(ctx.deps)
            attach_plan_snapshot(part, snapshot)
        if part.tool_call_id:
            ctx.deps.plan_snapshots[part.tool_call_id] = snapshot
        return True

    async def after_tool_execute(self, ctx, *, call, tool_def, args, result):
        if not self.capture_execute_results or self._executing_parent.get() is not None:
            return result
        # Normal run() has no consumer-facing tool-result events. Preserve the
        # settled result here while sibling tools may still be running. This is
        # a completed tool fact, deduplicated against its later official request;
        # later discovery availability parts are appended independently.
        if isinstance(result, ToolDenied):
            part = ToolReturnPart(call.tool_name, result.message, call.tool_call_id, outcome="denied")
        else:
            wrapped = result if isinstance(result, ToolReturn) else ToolReturn(result)
            part = ToolReturnPart.narrow_type(ToolReturnPart(
                call.tool_name, deepcopy(wrapped.return_value), call.tool_call_id,
                metadata=deepcopy(wrapped.metadata), tool_kind=getattr(tool_def, "tool_kind", None),
            ))
            if await self._capture_plan_result(ctx, part):
                # Carry the same snapshot into the official SDK request too;
                # do not amend an already committed tool result at finalization.
                wrapped.metadata = deepcopy(part.metadata)
                result = wrapped
        self._results[call.tool_call_id] = part
        await self._save(ctx, hook="after_tool_execute")
        return result


class StreamMessagePersistenceCapability(MessagePersistenceCapability):
    # Stream events already carry the fully normalized official result parts.
    capture_execute_results = False
    # The pre-tool hook keeps its authoritative scope check before dispatch.
    _duplicate_flush_hooks = frozenset({
        "_prepare_model_node", "after_model_request", "after_node_run",
        "wrap_run_event_stream", "stream_end",
    })

    async def wrap_run_event_stream(
        self, ctx: RunContext[AgentDeps], *, stream: AsyncIterable[AgentStreamEvent],
    ) -> AsyncIterable[AgentStreamEvent]:
        try:
            async for event in stream:
                if isinstance(event, FunctionToolCallEvent) and event.args_valid:
                    self._accept(ctx, event.part)
                    await self._save(
                        ctx, hook="wrap_run_event_stream", phase="tool_call", tool_call_id=event.part.tool_call_id,
                    )
                elif isinstance(event, FunctionToolResultEvent):
                    await self._capture_plan_result(ctx, event.part)
                    self._results[event.part.tool_call_id] = deepcopy(event.part)
                    await self._save(
                        ctx, hook="wrap_run_event_stream", phase="tool_result", tool_call_id=event.part.tool_call_id,
                    )
                yield event
            await self._save(ctx, hook="stream_end")
        finally:
            close = getattr(stream, "aclose", None)
            if callable(close):
                await close()
