"""Insert completed message facts at SDK boundaries; never persist live text snapshots."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from typing import Any

from pydantic_ai import AgentStreamEvent, ModelRequestNode, RunContext, ToolDenied
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, ToolSearch
from pydantic_ai.exceptions import ToolFailedError
from pydantic_ai.messages import (
    FunctionToolCallEvent, FunctionToolResultEvent, ModelMessage, ModelRequest,
    ModelResponse, ToolCallPart, ToolReturn, ToolReturnPart,
)
from pydantic_core import to_json

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_names import public_tool_messages
from deep_agent.agent.history import is_retry_feedback, is_user_only_request, same_tool_return
from deep_agent.agent.observability import log_event
from deep_agent.agent.planning import (
    attach_plan_snapshot, current_plan_snapshot, is_planning_tool, plan_snapshot_from_part,
)
from deep_agent.db.run_ownership import live_run
from deep_agent.db.execution_error import safe_execution_error
from deep_agent.db.serialization import serialize_message
from deep_agent.diagnostics.persistence import persistence_failure_fields
from deep_agent.usage import response_call


class MessagePersistenceError(RuntimeError):
    """Further tools must not execute after a completed-message write failure."""


class MessagePersistenceCapability(AbstractCapability[AgentDeps]):
    """Per-run instance, including provider retries; never share across Runs."""

    capture_execute_results = True
    _duplicate_flush_hooks: frozenset[str] = frozenset()

    def __init__(self, store: Any, *, history_length: int,
                 skip_user_prompt: bool, model_name: str | None = None,
                 provider_id: str | None = None,
                 event_queue: asyncio.Queue | None = None) -> None:
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
        self._committed_scope: tuple | None = None
        self._committed_projection: bytes | None = None
        self._message_identity_merges = 0
        self._response_id_mismatches = 0
        self._executing_parent: ContextVar[tuple[int, str] | None] = ContextVar(
            "deepagent_message_executing_parent", default=None,
        )

    def get_ordering(self) -> CapabilityOrdering:
        # Capture after the entire tool lifecycle, including other wrap hooks
        # that add timing or transform the finished result.
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

    def _accept(
        self, ctx: RunContext[AgentDeps], call: ToolCallPart, *, allow_nested: bool = False,
    ) -> tuple[int, str] | None:
        # Providers can reuse IDs in later loops: acceptance is response-scoped.
        for message in reversed(ctx.messages):
            if isinstance(message, ModelResponse) and any(
                isinstance(part, ToolCallPart) and part.tool_call_id == call.tool_call_id
                and part.tool_name == call.tool_name for part in message.parts
            ):
                self._record(message)
                key = (id(message), call.tool_call_id)
                self._accepted.add(key)
                return key
        # PTC dispatch uses normal SDK tool hooks for its authorized inner calls,
        # but those calls are not new model messages. Their durable outer tool is
        # already recorded; nested audit remains on its existing result metadata.
        # Context-local execution ancestry is stronger evidence than guessing a
        # parent from provider-controlled tool_call_id strings.
        parent = self._executing_parent.get()
        if allow_nested and parent is not None and parent in self._accepted:
            return None
        raise MessagePersistenceError("Accepted tool call has no official model response")

    def _with_ordinals(self, messages: list[ModelMessage]) -> list[ModelMessage]:
        result = []
        ordinal = 1 if self.skip_user_prompt else 0
        for message in messages:
            if self.skip_user_prompt and is_user_only_request(message):
                continue
            copy = deepcopy(message)
            copy.metadata = {**(copy.metadata or {}), "deepagent_message_ordinal": ordinal,
                             "deepagent_part_indices": list(range(len(copy.parts)))}
            ordinal += 1
            result.append(copy)
        return result

    def _completed_messages(self) -> list[ModelMessage]:
        messages = []
        ordinal = 1 if self.skip_user_prompt else 0
        accepted_in_round: set[str] = set()
        for message in self._ledger:
            if self.skip_user_prompt and is_user_only_request(message):
                continue
            copy = deepcopy(message)
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
                           if (copy.metadata or {}).get("deepagent_output_budget") or not is_retry_feedback(part)
                           or getattr(part, "tool_call_id", None) in accepted_in_round]
                copy.parts = [copy.parts[i] for i in indices]
                for part in copy.parts:
                    if is_retry_feedback(part) and not (copy.metadata or {}).get("deepagent_output_budget"):
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
                              if not is_retry_feedback(part)
                              or getattr(part, "tool_call_id", None) in accepted_in_round])
            for part in parts:
                if is_retry_feedback(part):
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

    async def _flush(self, ctx: RunContext[AgentDeps], *, hook: str = "unknown") -> None:
        async with self._write_lock:
            if self.persistence_error is not None:
                raise self.persistence_error
            deps = ctx.deps
            messages = []
            write_stage = "assemble"
            try:
                messages = self._completed_messages()
                scope = (deps.session_id, deps.agent_id, deps.user, deps.app_id,
                         deps.space_id, deps.turn_id, deps.run_id)
                projection = None
                if (
                    hook in self._duplicate_flush_hooks
                    and scope == self._committed_scope
                    and len(messages) == len(self.messages)
                ):
                    projection = self._projection(messages)
                    if self._committed_projection is None:
                        self._committed_projection = self._projection(self.messages)
                    if projection is not None and projection == self._committed_projection:
                        return
                write_stage = "append"
                async with asyncio.timeout(10):
                    rows = await self.store.append_message_parts(
                        deps.session_id, agent_id=deps.agent_id, user_id=deps.user,
                        app_id=deps.app_id, space_id=deps.space_id,
                        turn_id=deps.turn_id, run_id=deps.run_id,
                        messages=public_tool_messages(messages, deps.tool_names),
                        first_message_ordinal=1 if self.skip_user_prompt else 0,
                        usage_model_id=self.model_name,
                        usage_provider_id=self.provider_id,
                    )
                self.messages = messages
                self._committed_scope = scope
                self._committed_projection = projection
                write_stage = "publish"
                if self.event_queue is not None:
                    for row in rows or []:
                        self.event_queue.put_nowait({
                            "type": "session_event_appended", "persisted": True,
                            "event": row.public_dict(),
                        })
            except Exception as exc:
                log_event("message_persistence_failed", session_id=deps.session_id,
                          run_id=deps.run_id, agent_id=deps.agent_id,
                          phase="incremental", error_type=type(exc).__name__,
                          write_stage=write_stage,
                          message_identity_merges=self._message_identity_merges,
                          response_id_mismatches=self._response_id_mismatches,
                          **persistence_failure_fields(exc, store=self.store, messages=messages, hook=hook))
                self.persistence_error = MessagePersistenceError("Unable to persist completed message")
                raise self.persistence_error from exc

    async def _save(self, ctx: RunContext[AgentDeps], *, hook: str = "unknown") -> None:
        await self._flush(ctx, hook=hook)

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

    async def wrap_model_request(self, ctx, *, request_context, handler):
        response = await handler(request_context)
        if response.run_id is None:
            response.run_id = ctx.deps.run_id
        if ctx.deps.output_state is not None:
            await ctx.deps.output_state.check_response(ctx, response, progress=self)
        self._record(response)
        await self._save(ctx, hook="wrap_model_request")
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
        if self._accept(ctx, call, allow_nested=True):
            await self._save(ctx, hook="before_tool_execute")
        else:
            # Propagate an earlier persistence failure even if a nested tool caught it.
            await self._flush(ctx, hook="before_tool_execute")
        return args

    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
        previous_parent = self._executing_parent.get()
        # The latest SDK enters wrap before before_tool_execute. Establish
        # ancestry here so inner calls remain tied to the durable outer call.
        parent = previous_parent or self._accept(ctx, call, allow_nested=True)
        token = self._executing_parent.set(parent)
        try:
            result = await handler(args)
            if self.capture_execute_results and previous_parent is None:
                result = await self._capture_tool_result(ctx, call, tool_def, result)
            return result
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

    async def _capture_tool_result(self, ctx, call, tool_def, result):
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
        await self._save(ctx, hook="wrap_tool_execute")
        return result


class StreamMessagePersistenceCapability(MessagePersistenceCapability):
    # Stream events already carry the fully normalized official result parts.
    capture_execute_results = False
    _duplicate_flush_hooks = frozenset({"after_node_run", "stream_end"})

    async def wrap_run_event_stream(
        self, ctx: RunContext[AgentDeps], *, stream: AsyncIterable[AgentStreamEvent],
    ) -> AsyncIterable[AgentStreamEvent]:
        try:
            async for event in stream:
                if isinstance(event, FunctionToolCallEvent) and event.args_valid:
                    self._accept(ctx, event.part)
                    await self._save(ctx, hook="wrap_run_event_stream")
                elif isinstance(event, FunctionToolResultEvent):
                    await self._capture_plan_result(ctx, event.part)
                    self._results[event.part.tool_call_id] = deepcopy(event.part)
                    await self._save(ctx, hook="wrap_run_event_stream")
                yield event
            await self._flush(ctx, hook="stream_end")
        finally:
            close = getattr(stream, "aclose", None)
            if callable(close):
                await close()
