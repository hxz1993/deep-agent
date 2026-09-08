"""Durable previews from public SDK hooks, separate from canonical history."""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterable
from contextvars import ContextVar
from copy import deepcopy
from typing import Any

from pydantic_ai import AgentStreamEvent, ModelRequestNode, RunContext, ToolDenied
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.exceptions import ToolFailedError
from pydantic_ai.messages import (
    FunctionToolCallEvent, FunctionToolResultEvent, ModelMessage, ModelRequest,
    ModelResponse, PartDeltaEvent, PartStartEvent, ToolCallPart, ToolReturn, ToolReturnPart, UserPromptPart,
)

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.history import is_user_only_request
from deep_agent.db.run_ownership import HEARTBEAT_SECONDS, live_run


class RunProgressPersistenceError(RuntimeError):
    """Further tools must not execute without durable progress."""


class RunProgressCapability(AbstractCapability[AgentDeps]):
    """Per-run instance, including provider retries; never share across Runs."""

    snapshot_interval = 1.0
    heartbeat_interval = float(HEARTBEAT_SECONDS)
    capture_execute_results = True

    def __init__(self, store: Any, *, history_length: int,
                 skip_user_prompt: bool, model_name: str | None = None) -> None:
        self.store = store
        # Compatibility only: a fixed slice is invalid after SDK compaction.
        self.history_length = history_length
        self.skip_user_prompt = skip_user_prompt
        self.model_name = model_name
        self.revision = 0
        self.messages: list[ModelMessage] = []
        self.persistence_error: RunProgressPersistenceError | None = None
        self._ledger: list[ModelMessage] = []
        self._next_request: ModelRequest | None = None
        self._official_snapshot: list[ModelMessage] = []
        self._official_dirty = True
        self._accepted: set[tuple[int, str]] = set()
        self._parts: dict[int, Any] = {}
        self._partial_response: ModelResponse | None = None
        self._results: dict[str, Any] = {}
        self._result_contents: dict[str, UserPromptPart] = {}
        self._pending: list[ModelMessage] = []
        self._pending_version = 0
        self._written_version = 0
        self._last_write = 0.0
        self._last_heartbeat = 0.0
        self._write_lock = asyncio.Lock()
        self._executing_parent: ContextVar[tuple[int, str] | None] = ContextVar(
            "deepagent_progress_executing_parent", default=None,
        )

    def get_ordering(self) -> CapabilityOrdering:
        # The public SDK orders after_* hooks in reverse. Outermost means our
        # after_tool_execute observes the existing limits/redaction's final
        # result, never the raw oversized result before those policies run.
        return CapabilityOrdering(position="outermost")

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
        self._official_dirty = True
        for index, existing in enumerate(self._ledger):
            if self._same_message(existing, message):
                if existing is not message:
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
        for message in official:
            match = next((index for index, previous in enumerate(merged)
                          if self._same_message(previous, message)), None)
            if match is None:
                merged.append(message)
            else:
                merged[match] = message
        return deepcopy(merged)

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
        raise RunProgressPersistenceError("Accepted tool call has no official model response")

    def _snapshot(self, ctx: RunContext[AgentDeps]) -> list[ModelMessage]:
        if self._official_dirty:
            frozen: list[ModelMessage] = []
            for message in self._ledger:
                if self.skip_user_prompt and is_user_only_request(message):
                    continue
                copy = deepcopy(message)
                if isinstance(copy, ModelResponse):
                    copy.parts = [part for part in copy.parts if not isinstance(part, ToolCallPart)
                                  or (id(message), part.tool_call_id) in self._accepted]
                if copy.parts:
                    frozen.append(copy)
            self._official_snapshot = frozen
            self._official_dirty = False
        # Frozen official messages can include megabytes of tool output. Reuse
        # this immutable prefix between token deltas; only a throttled DB flush
        # copies it again, rather than doing O(history_size) work on every token.
        messages = list(self._official_snapshot)
        if self._parts:
            if self._partial_response is None:
                self._partial_response = ModelResponse(
                    parts=[], model_name=self.model_name, run_id=ctx.deps.run_id,
                    state="incomplete",
                )
            preview = deepcopy(self._partial_response)
            # Streamed arguments have not passed SDK validation. Native calls
            # have no local validation and remain non-actionable evidence.
            preview.parts = [deepcopy(part) for _, part in sorted(self._parts.items())
                             if not isinstance(part, ToolCallPart)]
            if preview.parts:
                messages.append(preview)
        if self._results:
            # Current phase only; after_node records the official request and
            # clears these not-yet-in-history parallel returns.
            messages.append(ModelRequest(parts=deepcopy([
                *self._results.values(), *self._result_contents.values(),
            ])))
        return messages

    def _stage(self, ctx: RunContext[AgentDeps]) -> None:
        # Freeze on the SDK driver task, not in the background writer: SDK
        # compaction/stream mutation may change the live objects in place.
        self._pending = self._snapshot(ctx)
        self._pending_version += 1

    async def _flush(self, ctx: RunContext[AgentDeps]) -> None:
        async with self._write_lock:
            if self.persistence_error is not None:
                raise self.persistence_error
            if self._written_version == self._pending_version:
                return
            version, messages = self._pending_version, deepcopy(self._pending)
            self.revision += 1
            deps = ctx.deps
            try:
                async with asyncio.timeout(10):
                    saved = await self.store.save_run_progress(
                        deps.session_id, agent_id=deps.agent_id, user_id=deps.user,
                        app_id=deps.app_id, space_id=deps.space_id,
                        turn_id=deps.turn_id, run_id=deps.run_id,
                        messages=messages, revision=self.revision,
                        first_message_ordinal=1 if self.skip_user_prompt else 0,
                    )
                if saved is None:
                    raise RunProgressPersistenceError("Run progress was rejected as stale or terminal")
            except Exception as exc:
                error = RunProgressPersistenceError("Unable to persist running conversation progress")
                self.persistence_error = error
                raise error from exc
            self.messages = messages
            self._written_version = version
            self._last_write = self._last_heartbeat = time.monotonic()

    async def _save(self, ctx: RunContext[AgentDeps], *, force: bool = True) -> None:
        self._stage(ctx)
        if force or time.monotonic() - self._last_write >= self.snapshot_interval:
            await self._flush(ctx)

    async def _heartbeat(self, ctx: RunContext[AgentDeps]) -> None:
        touch = getattr(self.store, "touch_run_progress", None)
        if self.revision == 0 or not callable(touch):
            return
        async with self._write_lock:
            async with asyncio.timeout(10):
                if not await touch(ctx.deps.session_id, run_id=ctx.deps.run_id):
                    raise RunProgressPersistenceError("Running conversation lease was lost")
            self._last_heartbeat = time.monotonic()

    async def wrap_node_run(self, ctx, *, node, handler):
        """Own a writer per node; never move SDK iterator consumption to it.

        Covers provider silence, tools and model hooks (including compaction).
        All exits drain the writer before leaving task-local SDK contexts.
        """
        # All before_node hooks (notably pending-message drain) have finished;
        # capture the request actually handed to the SDK, not its earlier form.
        await self._prepare_model_node(ctx, node)
        owner = asyncio.current_task()
        assert owner is not None
        failure: BaseException | None = None
        issued_cancel = False

        async def writer() -> None:
            nonlocal failure, issued_cancel
            try:
                while True:
                    await asyncio.sleep(min(self.snapshot_interval, self.heartbeat_interval))
                    now = time.monotonic()
                    if self._pending_version != self._written_version:
                        await self._flush(ctx)
                    elif now - self._last_heartbeat >= self.heartbeat_interval:
                        await self._heartbeat(ctx)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure = exc
                self.persistence_error = (exc if isinstance(exc, RunProgressPersistenceError)
                                          else RunProgressPersistenceError("Unable to renew running conversation progress"))
                # Wake a silent provider/tool, retracting only our cancellation.
                # An independent user cancellation must continue to win.
                if not owner.done() and not owner.cancelling():
                    issued_cancel = True
                    owner.cancel()

        task = asyncio.create_task(writer(), name="deepagent-run-progress-writer")
        try:
            try:
                result = await handler(node)
            except BaseException:
                if issued_cancel:
                    owner.uncancel()
                if failure is not None and not owner.cancelling():
                    raise self.persistence_error from failure
                raise
            if failure is not None:
                if issued_cancel:
                    owner.uncancel()
                raise self.persistence_error from failure
            return result
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def wrap_run(self, ctx, *, handler):
        # The SDK uses this hook for streaming and ordinary runs alike. Its
        # finally is guaranteed even when cancellation skips after_run hooks.
        with live_run(ctx.deps.run_id):
            return await handler()

    async def _prepare_model_node(self, ctx, node):
        if isinstance(node, ModelRequestNode):
            self._parts.clear()
            self._partial_response = None
            self._results.clear()
            self._result_contents.clear()
            if not node.is_resuming_without_prompt:
                # after_node may expose the next request before other public
                # before_node hooks replace it. It has not entered SDK history
                # yet, so replace that pending entry rather than duplicating it.
                if self._next_request is not None and self._next_request is not node.request:
                    self._ledger = [message for message in self._ledger if message is not self._next_request]
                self._record(node.request)
            self._next_request = None
            await self._save(ctx)

    async def after_model_request(self, ctx, *, request_context, response):
        self._parts.clear()
        self._partial_response = None
        self._record(response)
        await self._save(ctx)
        return response

    async def after_node_run(self, ctx, *, node, result):
        self._parts.clear()
        self._partial_response = None
        if isinstance(result, ModelRequestNode) and not result.is_resuming_without_prompt:
            self._record(result.request)
            self._next_request = result.request
            self._results.clear()
            self._result_contents.clear()
        # A CallToolsNode returned by UserPromptNode on deferred resume is the
        # previous Run's pending response, not a new message.
        if isinstance(node, ModelRequestNode):
            response = getattr(result, "model_response", None)
            if isinstance(response, ModelResponse):
                self._record(response)
        await self._save(ctx)
        return result

    async def before_tool_execute(self, ctx, *, call, tool_def, args):
        if self._accept(ctx, call, allow_nested=True):
            await self._save(ctx)
        else:
            # Also propagates an earlier timer failure if a sandbox caught it.
            await self._flush(ctx)
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
                await self._save(ctx)
            raise
        finally:
            self._executing_parent.reset(token)

    async def after_tool_execute(self, ctx, *, call, tool_def, args, result):
        if not self.capture_execute_results or self._executing_parent.get() is not None:
            return result
        # Normal run() has no consumer-facing tool-result events. Preserve the
        # settled result here while sibling tools may still be running. This is
        # a recovery/display snapshot only; the next official ModelRequest
        # replaces it, including SDK timestamps and tool-discovery availability.
        if isinstance(result, ToolDenied):
            part = ToolReturnPart(call.tool_name, result.message, call.tool_call_id, outcome="denied")
        else:
            wrapped = result if isinstance(result, ToolReturn) else ToolReturn(result)
            part = ToolReturnPart.narrow_type(ToolReturnPart(
                call.tool_name, deepcopy(wrapped.return_value), call.tool_call_id,
                metadata=deepcopy(wrapped.metadata), tool_kind=getattr(tool_def, "tool_kind", None),
            ))
            if wrapped.content:
                self._result_contents[call.tool_call_id] = UserPromptPart(deepcopy(wrapped.content))
        self._results[call.tool_call_id] = part
        await self._save(ctx)
        return result


class StreamRunProgressCapability(RunProgressCapability):
    # Stream events already carry the fully normalized official result parts.
    capture_execute_results = False

    async def wrap_run_event_stream(
        self, ctx: RunContext[AgentDeps], *, stream: AsyncIterable[AgentStreamEvent],
    ) -> AsyncIterable[AgentStreamEvent]:
        try:
            async for event in stream:
                if isinstance(event, PartStartEvent):
                    self._parts[event.index] = deepcopy(event.part)
                    await self._save(ctx)
                elif isinstance(event, PartDeltaEvent) and event.index in self._parts:
                    self._parts[event.index] = event.delta.apply(self._parts[event.index])
                    await self._save(ctx, force=False)
                elif isinstance(event, FunctionToolCallEvent) and event.args_valid:
                    self._parts.clear()
                    self._partial_response = None
                    self._accept(ctx, event.part)
                    await self._save(ctx)
                elif isinstance(event, FunctionToolResultEvent):
                    self._parts.clear()
                    self._partial_response = None
                    self._results[event.part.tool_call_id] = deepcopy(event.part)
                    await self._save(ctx)
                yield event
            await self._flush(ctx)
        finally:
            close = getattr(stream, "aclose", None)
            if callable(close):
                await close()
