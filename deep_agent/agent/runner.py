from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any, AsyncIterator

from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    CancellationToken,
    DeferredToolRequests,
    DeferredToolResults,
    RunContext,
)
from pydantic_ai.exceptions import RunCancelled
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    LoadCapabilityCallPart,
    LoadCapabilityReturnPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserContent,
    UserPromptPart,
)
from pydantic_ai.settings import ModelSettings
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.usage import RunUsage, UsageLimits

from deep_agent.agent import events
from deep_agent.agent.active_runs import ActiveRun, ActiveRunRegistry
from deep_agent.agent.context.budget import build_context_budget
from deep_agent.agent.context.compaction import (
    DeepAgentContextCompaction,
    SummaryUnavailable,
    is_provider_context_overflow,
    summary_contents,
)
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.context.manual import manual_compaction_availability
from deep_agent.agent.context_references import (
    history_has_context_references,
    project_context_references_in_history,
)
from deep_agent.agent.context.runtime import ContextRunState
from deep_agent.agent.attachments import (
    has_user_turn,
    index_attachment_refs,
    refs_from_attachments,
    resolve_history_for_llm,
    resolve_user_prompt,
)
from deep_agent.agent.deferred import serialize_deferred
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister, PersistEvent
from deep_agent.agent.limits import format_stream_error
from deep_agent.agent.observability import (
    cache_usage_available,
    log_event,
    log_model_requests,
    run_metrics,
    usage_dict,
)
from deep_agent.agent.planning import (
    attach_known_plan_snapshots,
    attach_plan_snapshot,
    current_plan_snapshot,
    is_planning_tool,
    prepare_plan_store,
    restore_plan_snapshot,
)
from deep_agent.agent.run_identity import conversation_id_for, new_run_id
from deep_agent.agent.tool_output_limits import PlatformToolResultStore
from deep_agent.agent.history import (
    _extract_assistant_text,
    first_user_prompt,
    incremental_append_messages,
    mark_cancelled_response_interrupted,
    repair_unprocessed_tool_calls,
)
from deep_agent.capabilities.ui.registry import enrich_deferred, enrich_tool_event
from loguru import logger

from deep_agent.db.conversation import SessionStore
from deep_agent.db.stores.bundle import DataStores
from deep_agent.db.serialization import serialize_message
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.schemas.context_reference import ContextReference
from deep_agent.options import OPTION

_STREAM_OUTPUT_TYPE: list[Any] = [str, DeferredToolRequests]


@dataclass(frozen=True)
class _PreparedHistory:
    projected: list[ModelMessage]
    raw: list[ModelMessage]
    pre_persisted_user: bool


@dataclass(frozen=True)
class _RunTerminalContext:
    session_id: str
    deps: AgentDeps
    mode: str


def _should_publish_tool_call(
    event: Any,
    mapped: dict[str, Any],
) -> bool:
    """Publish function calls only after Pydantic AI has validated their arguments."""
    if mapped.get("type") != "tool_call":
        return True
    if isinstance(event, FunctionToolCallEvent):
        return event.args_valid is True
    # Provider-native calls are not FunctionToolCallEvent instances and retain
    # their native PartStartEvent stream. Plain ToolCallPart starts are only a
    # provisional model emission; a validated FunctionToolCallEvent follows.
    return not isinstance(getattr(event, "part", None), ToolCallPart)


def _history_hash(messages: list[ModelMessage]) -> str:
    payload = [serialize_message(message) for message in messages]
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AgentRunner:
    """Transport-agnostic wrapper around a Pydantic AI agent.

    ``run`` / ``run_stream`` take a session id + prompt, load history from the
    store, run the agent loop (managed by Pydantic AI), persist new messages,
    and (for streaming) map Pydantic AI events to plain serializable dicts.

    This class has no FastAPI dependency, so it can later be wrapped as a node
    in a pydantic-graph orchestration without changes.
    """

    def __init__(
        self,
        agent: Agent[AgentDeps, Any],
        store: SessionStore,
        usage_limits: UsageLimits,
        *,
        ui_tool_hints: dict[str, dict[str, Any]] | None = None,
        model_settings: ModelSettings | None = None,
        context_window_tokens: int | None = None,
        max_output_tokens: int | None = None,
        model_tags: list[str] | None = None,
        context_compaction: DeepAgentContextCompaction | None = None,
        summary_model_id: str | None = None,
        model_id: str | None = None,
        estimator: TokenEstimator | None = None,
        stores: DataStores | None = None,
        active_runs: ActiveRunRegistry | None = None,
        skill_runtime_context: dict[str, Any] | None = None,
        tool_output_store: PlatformToolResultStore | None = None,
    ) -> None:
        self.agent = agent
        self.store = store
        self.usage_limits = usage_limits
        self.ui_tool_hints = ui_tool_hints or {}
        self.model_settings = model_settings
        self._context_window_tokens = context_window_tokens
        self._max_output_tokens = max_output_tokens
        self._model_tags = list(model_tags or [])
        self._context_compaction = context_compaction
        self._summary_model_id = summary_model_id
        self._model_id = model_id or summary_model_id
        self._estimator = estimator or TokenEstimator.conservative()
        self._stores = stores
        self._active_runs = active_runs or ActiveRunRegistry()
        self._skill_runtime_context = skill_runtime_context
        self._tool_output_store = tool_output_store

    def _bind_tool_output_store(
        self,
        deps: AgentDeps,
        raw_history: list[ModelMessage],
    ) -> None:
        if self._tool_output_store is not None:
            self._tool_output_store.bind(deps, raw_history)

    @staticmethod
    def _start_run_identity(
        session_id: str,
        deps: AgentDeps,
        *,
        run_id: str | None = None,
    ) -> None:
        if deps.conversation_id is None:
            deps.conversation_id = conversation_id_for(deps.agent_id, session_id)
        # A fresh Pydantic run id is required for every send/resume/retry.  The
        # streaming API may pre-mint one so it can publish it in the first SSE.
        deps.run_id = run_id or new_run_id()
        deps.plan_store = None
        deps.plan_snapshots = {}

    async def _ensure_turn_identity(
        self,
        session_id: str,
        deps: AgentDeps,
        *,
        message: str,
        attachments: list[AttachmentRef] | None,
        context_refs: list[ContextReference] | None,
        deferred_tool_results: DeferredToolResults | None,
    ) -> None:
        if has_user_turn(message, attachments, context_refs):
            deps.turn_id = deps.run_id
            deps.resume_from_run_id = None
            return
        if deps.turn_id is not None:
            return
        deps.resume_from_run_id = None
        if deferred_tool_results:
            call_ids = sorted(
                set(deferred_tool_results.calls)
                | set(deferred_tool_results.approvals)
                | set(deferred_tool_results.metadata)
            )
            find_origin = getattr(self.store, "find_call_origin", None)
            origin = (
                await find_origin(session_id, call_ids)
                if callable(find_origin) and call_ids
                else None
            )
            if origin is None:
                raise ValueError("Deferred tool results do not match a persisted tool call")
            deps.turn_id, deps.resume_from_run_id = origin
            return
        deps.turn_id = deps.turn_id or deps.run_id

    async def _prepare_plan_store(self, session_id: str, deps: AgentDeps) -> None:
        snapshot = None
        if deps.resume_from_run_id and deps.turn_id:
            latest_snapshot = getattr(self.store, "latest_plan_snapshot", None)
            if callable(latest_snapshot):
                snapshot = await latest_snapshot(session_id, deps.turn_id)
        await restore_plan_snapshot(deps, snapshot)

    async def _start_run_event(self, session_id: str, deps: AgentDeps, *, mode: str):
        # This is an in-memory context only. Normal runs never persist a Run row.
        return _RunTerminalContext(session_id=session_id, deps=deps, mode=mode)

    async def _finish_run_event(
        self,
        run_event: _RunTerminalContext,
        *,
        status: str,
        started_at: float,
        usage: RunUsage | None,
        error: str | None = None,
    ):
        if status not in {"failed", "cancelled", "interrupted"}:
            return None
        deps = run_event.deps
        append_terminal = getattr(self.store, "append_terminal", None)
        if not callable(append_terminal):
            return None
        payload = {
            "mode": run_event.mode,
            "model_id": self._model_id,
            "duration_ms": max(0, round((time.perf_counter() - started_at) * 1_000)),
            "usage": _run_usage_dict(usage),
            "error": {"code": error} if error else None,
        }
        return await append_terminal(
            run_event.session_id,
            status=status,
            agent_id=deps.agent_id,
            user_id=deps.user,
            app_id=deps.app_id,
            space_id=deps.space_id,
            turn_id=deps.turn_id,
            run_id=deps.run_id,
            payload=payload,
        )

    @staticmethod
    async def _capture_planning_result(event: Any, deps: AgentDeps) -> None:
        """Attach a confirmed Store snapshot to an accepted Planning result."""

        if not isinstance(event, FunctionToolResultEvent):
            return
        part = event.part
        if not isinstance(part, ToolReturnPart) or not is_planning_tool(part.tool_name):
            return
        snapshot = await current_plan_snapshot(deps)
        attach_plan_snapshot(part, snapshot)
        if part.tool_call_id:
            deps.plan_snapshots[part.tool_call_id] = snapshot

    @staticmethod
    async def _attach_final_plan_snapshot(
        messages: list[ModelMessage],
        deps: AgentDeps,
    ) -> None:
        """Guarantee a final snapshot for non-streaming and terminal persistence."""

        attach_known_plan_snapshots(
            messages,
            deps.plan_snapshots,
            final_snapshot=await current_plan_snapshot(deps),
        )

    def _prepare_skill_runtime(self, deps: AgentDeps) -> None:
        context = self._skill_runtime_context
        if context is None:
            deps.skill_runtime = None
            deps.loaded_skill_codes = []
            return
        deps.skill_runtime = {
            "configured": list(context.get("configured", [])),
            "available": list(context.get("available", [])),
            "loaded": [],
            "unavailable": [dict(item) for item in context.get("unavailable", [])],
        }
        deps.loaded_skill_codes = []

    @staticmethod
    def _record_loaded_skills(
        deps: AgentDeps,
        messages: list[ModelMessage],
    ) -> None:
        calls: dict[str, str] = {}
        loaded: list[str] = []
        for message in messages:
            for part in message.parts:
                if isinstance(part, LoadCapabilityCallPart):
                    capability_id = part.capability_id
                    if capability_id:
                        calls[part.tool_call_id] = capability_id
                elif (
                    isinstance(part, LoadCapabilityReturnPart)
                    and part.outcome == "success"
                ):
                    capability_id = calls.get(part.tool_call_id)
                    if capability_id and capability_id not in loaded:
                        loaded.append(capability_id)
        deps.loaded_skill_codes = loaded
        if deps.skill_runtime is not None:
            deps.skill_runtime["loaded"] = list(loaded)

    @staticmethod
    def _compaction_name(deps: AgentDeps) -> str:
        state = deps.context_state
        if state is None or not state.telemetry.triggered:
            return "none"
        return state.telemetry.strategy

    @asynccontextmanager
    async def _session_lock(self, session_id: str, *, agent_id: str | None) -> AsyncIterator[None]:
        lock = getattr(self.store, "lock", None)
        if callable(lock):
            async with lock(session_id, agent_id=agent_id):
                yield
        else:
            yield

    @property
    def manual_compaction_enabled(self) -> bool:
        return self._context_compaction is not None

    async def run_manual_compaction(
        self,
        session_id: str,
        deps: AgentDeps,
    ) -> AsyncIterator[dict[str, Any]]:
        """Force one persisted compaction Event without appending a chat turn."""

        self._start_run_identity(session_id, deps)
        self._prepare_skill_runtime(deps)
        started_at = time.perf_counter()

        def manual_result(
            status_name: str,
            reason: str | None,
            *,
            usage: RunUsage | None = None,
            telemetry: dict[str, Any] | None = None,
            target_tokens: int | None = None,
            compaction_event: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            metrics = run_metrics(
                status=status_name,
                started_at=started_at,
                model_id=self._model_id,
                usage=usage,
                compaction="manual",
            )
            payload = _manual_compaction_result(
                status_name,
                reason,
                usage=usage,
                telemetry=telemetry,
                target_tokens=target_tokens,
                compaction_event=compaction_event,
                conversation_id=deps.conversation_id,
                run_id=deps.run_id,
                run_metrics=metrics,
            )
            log_event(
                "manual_compaction_completed",
                conversation_id=deps.conversation_id,
                run_id=deps.run_id,
                agent_id=deps.agent_id,
                session_id=session_id,
                reason=reason,
                **metrics,
            )
            return payload

        log_event(
            "manual_compaction_started",
            conversation_id=deps.conversation_id,
            run_id=deps.run_id,
            agent_id=deps.agent_id,
            session_id=session_id,
            model_id=self._model_id,
        )

        if not self.manual_compaction_enabled:
            yield manual_result("failed", "context_window_unknown")
            return
        assert self._context_compaction is not None

        async with self._session_lock(session_id, agent_id=deps.agent_id):
            raw_reader = getattr(self.store, "get_raw_history", self.store.get_history)
            raw = await raw_reader(session_id, agent_id=deps.agent_id)
            if not raw:
                yield manual_result("noop", "empty_history")
                return
            visible = await self.store.get_history(session_id, agent_id=deps.agent_id)
            deps.has_context_references = history_has_context_references(visible)
            projected = project_context_references_in_history(visible)
            budget = build_context_budget(
                projected,
                context_window_tokens=self._context_window_tokens,
                max_output_tokens=self._max_output_tokens,
                estimator=self._estimator,
                output_history=raw,
                estimate_scope="history_only",
            )
            availability = manual_compaction_availability(
                raw,
                budget,
            )
            if not availability.available:
                outcome = (
                    "failed"
                    if availability.reason in {"context_window_unknown", "pending_deferred"}
                    else "noop"
                )
                yield manual_result(outcome, availability.reason)
                return
            configured_target = budget.target_tokens or budget.estimated_history_tokens
            target_tokens = max(
                1,
                min(
                    configured_target,
                    int(
                        budget.estimated_history_tokens
                        * OPTION.agent.context_compaction_target_ratio
                    ),
                ),
            )
            original_hash = _history_hash(raw)
            usage = RunUsage()
            event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            state = ContextRunState(
                raw_history=list(raw),
                estimator=self._estimator,
                usage_limits=self.usage_limits,
                event_queue=event_queue,
            )
            deps.context_state = state
            self._bind_compaction_lifecycle(session_id=session_id, deps=deps)
            deps.attachment_by_id = index_attachment_refs(messages=raw)
            request_context = ModelRequestContext(
                model=self.agent.model,
                messages=projected,
                model_settings=self.model_settings,
                model_request_parameters=ModelRequestParameters(),
            )
            run_context = RunContext(
                deps=deps,
                model=self.agent.model,
                usage=usage,
                usage_limits=self.usage_limits,
                agent=self.agent,
                messages=projected,
                model_settings=self.model_settings,
            )
            task = asyncio.create_task(
                self._context_compaction.compact_manually(
                    run_context,
                    request_context,
                    state=state,
                    target_tokens=target_tokens,
                )
            )
            queue_task: asyncio.Task[Any] | None = asyncio.create_task(event_queue.get())
            try:
                while not task.done():
                    done, _ = await asyncio.wait(
                        {task, queue_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if queue_task in done:
                        yield queue_task.result()
                        queue_task = asyncio.create_task(event_queue.get())
                while not event_queue.empty():
                    yield event_queue.get_nowait()
                compacted = task.result()
            except SummaryUnavailable as exc:
                reason = exc.reason
                await state.finish_compaction(
                    "timed_out" if reason == "summary_timeout" else "failed",
                    reason,
                )
                while not event_queue.empty():
                    yield event_queue.get_nowait()
                insufficient_reasons = {
                    "no_safe_summary_prefix",
                    "no_safe_summary_units",
                    "summary_unit_too_large",
                }
                no_reduction_reasons = {
                    "no_new_summary_content",
                    "summary_not_applied",
                    "target_not_met",
                }
                status_name = (
                    "noop"
                    if reason in insufficient_reasons | no_reduction_reasons
                    else "failed"
                )
                result_reason = (
                    "insufficient_history"
                    if reason in insufficient_reasons
                    else "no_reduction"
                    if reason in no_reduction_reasons
                    else reason
                )
                yield manual_result(
                    status_name,
                    result_reason,
                    usage=usage,
                    telemetry=state.telemetry.as_dict(),
                    target_tokens=target_tokens,
                )
                return
            except asyncio.CancelledError:
                await state.finish_compaction("cancelled", "cancelled")
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                raise
            except Exception as exc:  # provider errors must preserve the prior surface
                await state.finish_compaction("failed", type(exc).__name__)
                while not event_queue.empty():
                    yield event_queue.get_nowait()
                yield manual_result(
                    "failed",
                    type(exc).__name__,
                    usage=usage,
                    telemetry=state.telemetry.as_dict(),
                    target_tokens=target_tokens,
                )
                return
            finally:
                if queue_task is not None and not queue_task.done():
                    queue_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await queue_task

            current_raw = await raw_reader(session_id, agent_id=deps.agent_id)
            if _history_hash(current_raw) != original_hash:
                await state.finish_compaction("stale", "source_history_changed")
                while not event_queue.empty():
                    yield event_queue.get_nowait()
                yield manual_result(
                    "failed",
                    "session_changed",
                    usage=usage,
                    telemetry=state.telemetry.as_dict(),
                    target_tokens=target_tokens,
                )
                return
            yield {
                "type": "context_compaction_progress",
                "persisted": False,
                "event_id": state.compaction_event_id,
                "phase": "committing",
                "mode": "manual",
                "message": "正在保存压缩事件…",
            }
            try:
                event = await self._persist_compaction(
                    session_id, deps, raw_history=raw, projected_history=compacted,
                    trigger="manual",
                    started_at=state.compaction_started_at or started_at,
                    target_tokens=target_tokens,
                )
            except asyncio.CancelledError:
                await state.finish_compaction("cancelled", "cancelled")
                while not event_queue.empty():
                    yield event_queue.get_nowait()
                raise
            except Exception as exc:
                error_code = type(exc).__name__
                logger.warning(
                    "Manual context compaction Event save failed for {}/{}: {}",
                    deps.agent_id,
                    session_id,
                    error_code,
                )
                try:
                    await state.finish_compaction("failed", error_code)
                except Exception as lifecycle_exc:
                    logger.warning(
                        "Manual context compaction failure status save failed for {}/{}: {}",
                        deps.agent_id,
                        session_id,
                        type(lifecycle_exc).__name__,
                    )
                while not event_queue.empty():
                    yield event_queue.get_nowait()
                yield manual_result(
                    "failed",
                    error_code,
                    usage=usage,
                    telemetry=state.telemetry.as_dict(),
                    target_tokens=target_tokens,
                )
                return
            if event is None:
                await state.finish_compaction("stale", "source_history_changed")
                while not event_queue.empty():
                    yield event_queue.get_nowait()
                yield manual_result(
                    "failed",
                    "session_changed",
                    usage=usage,
                    telemetry=state.telemetry.as_dict(),
                    target_tokens=target_tokens,
                )
                return
            yield {
                "type": "session_event_updated",
                "persisted": True,
                "event": event.public_dict(),
            }
            yield manual_result(
                "completed",
                None,
                usage=usage,
                telemetry=state.telemetry.as_dict(),
                target_tokens=target_tokens,
                compaction_event={
                    "event_id": event.event_id,
                    "through_sequence": event.payload["source"]["through_sequence"],
                    "projected_estimated_tokens": event.payload["budget"]["estimated_after"],
                    "summary_model_id": self._summary_model_id,
                    "updated_at": event.updated_at.isoformat(),
                },
            )

    async def _prepare_history(
        self,
        session_id: str,
        message: str,
        deps: AgentDeps,
        *,
        attachments: list[AttachmentRef] | None = None,
        context_refs: list[ContextReference] | None = None,
        branch_from: int | None = None,
        deferred_tool_results: DeferredToolResults | None = None,
        session_event_queue: asyncio.Queue[dict[str, Any]] | None = None,
    ) -> _PreparedHistory:
        if branch_from is not None:
            raise ValueError("Branch From is not implemented for session events")
        raw_reader = getattr(self.store, "get_raw_history", self.store.get_history)
        raw_history = await raw_reader(session_id, agent_id=deps.agent_id)
        history = await self.store.get_history(session_id, agent_id=deps.agent_id)
        deps.turn_id = deps.turn_id or deps.run_id
        if not deferred_tool_results:
            repaired = repair_unprocessed_tool_calls(history)
            if repaired is not history:
                history = repaired
        pre_persisted_user = False
        if has_user_turn(message, attachments, context_refs):
            content = refs_from_attachments(message, attachments, context_refs)
            user_request = ModelRequest(parts=[UserPromptPart(content=content)])
            rows = await self.store.append(
                session_id,
                [user_request],
                agent_id=deps.agent_id,
                user_id=deps.user,
                app_id=deps.app_id,
                space_id=deps.space_id,
                turn_id=deps.turn_id,
                run_id=deps.run_id,
            )
            if session_event_queue is not None:
                for row in rows:
                    session_event_queue.put_nowait(
                        {
                            "type": "session_event_appended",
                            "persisted": True,
                            "event": row.public_dict(),
                        }
                    )
            history = [*history, user_request]
            raw_history = [*raw_history, user_request]
            pre_persisted_user = True
        deps.attachment_by_id = index_attachment_refs(
            attachments=attachments,
            messages=history,
        )
        deps.has_context_references = history_has_context_references(history)
        return _PreparedHistory(
            projected=history,
            raw=raw_history,
            pre_persisted_user=pre_persisted_user,
        )

    def _context_status(
        self,
        history: list[ModelMessage],
        deps: AgentDeps | None = None,
    ) -> dict[str, Any]:
        state = deps.context_state if deps is not None else None
        budget = (
            state.last_budget
            if state is not None and state.last_budget is not None
            else None
        )
        if budget is None:
            budget = build_context_budget(
                history,
                context_window_tokens=self._context_window_tokens,
                max_output_tokens=self._max_output_tokens,
                estimator=self._estimator,
                output_history=history,
            )
        telemetry = (
            state.telemetry.as_dict()
            if state is not None
            else {
                "triggered": False,
                "trigger_count": 0,
                "strategy": "none",
                "target_met": False,
                "estimated_tokens_before": budget.estimated_history_tokens,
                "estimated_tokens_after": budget.estimated_history_tokens,
                "max_estimated_tokens_before": budget.estimated_history_tokens,
                "messages_before": len(history),
                "messages_after": len(history),
                "reduction_ratio": 0.0,
                "summary_applied": False,
                "summary_failed": False,
                "summary_calls": 0,
                "summary_chunks": 0,
                "fallback_reason": None,
                "failure": None,
            }
        )
        result = {"budget": budget.as_dict(), "compaction": telemetry}
        if deps is not None and deps.tool_discovery is not None:
            result["tool_discovery"] = deps.tool_discovery.as_dict()
        if deps is not None and deps.skill_runtime is not None:
            result["skills"] = {
                "configured": list(deps.skill_runtime.get("configured", [])),
                "available": list(deps.skill_runtime.get("available", [])),
                "loaded": list(deps.loaded_skill_codes),
                "unavailable": [
                    dict(item) for item in deps.skill_runtime.get("unavailable", [])
                ],
            }
        if deps is not None and deps.tool_output_limits is not None:
            result["tool_output_limits"] = deps.tool_output_limits.as_dict()
        return result

    def _prepare_context_state(
        self,
        deps: AgentDeps,
        prepared: _PreparedHistory,
        *,
        event_queue: asyncio.Queue[dict[str, Any]] | None = None,
    ) -> None:
        if self._context_compaction is None:
            deps.context_state = None
            return
        deps.context_state = ContextRunState(
            raw_history=list(prepared.raw),
            estimator=self._estimator,
            usage_limits=self.usage_limits,
            event_queue=event_queue,
        )
        self._bind_compaction_lifecycle(session_id=deps.session_id, deps=deps)

    def _bind_compaction_lifecycle(self, *, session_id: str, deps: AgentDeps) -> None:
        state = deps.context_state
        if state is None:
            return
        started: dict[str, tuple[float, dict[str, Any]]] = {}

        async def start(trigger: str):
            payload = {
                "trigger": trigger,
                "previous_compaction_event_id": None,
                "source": None,
                "replacement": None,
                "budget": None,
                "pruned_tool_results": [],
                "summary_call": None,
                "duration_ms": None,
                "error": None,
            }
            event = await self.store.append_lifecycle(
                session_id,
                event_type="compaction",
                status="running",
                agent_id=deps.agent_id,
                user_id=deps.user,
                app_id=deps.app_id,
                space_id=deps.space_id,
                turn_id=deps.turn_id,
                run_id=deps.run_id,
                payload=payload,
            )
            started[event.event_id] = (time.perf_counter(), payload)
            return event

        async def finish(status: str, error: str | None):
            event_id = state.compaction_event_id
            if event_id is None:
                return None
            started_at, payload = started.get(event_id, (time.perf_counter(), {}))
            terminal_payload = {
                **payload,
                "duration_ms": max(
                    0, round((time.perf_counter() - started_at) * 1_000)
                ),
                "error": {"code": error} if error else None,
            }
            return await self.store.update_lifecycle(
                event_id, status=status, payload=terminal_payload
            )

        state.start_compaction_event = start
        state.finish_compaction_event = finish

    async def _maybe_save_compaction(
        self,
        session_id: str,
        deps: AgentDeps,
        *,
        projected_history: list[ModelMessage],
        expected_row_version: int = 0,
    ) -> Any | bool | None:
        del expected_row_version
        if (
            self._context_compaction is None
            or deps.context_state is None
            or not deps.context_state.telemetry.summary_applied
            or deps.context_state.telemetry.summary_failed
            or not deps.context_state.telemetry.target_met
            or deps.context_state.telemetry.strategy == "original_history_fallback"
        ):
            return None
        target = (
            deps.context_state.last_budget.target_tokens
            if deps.context_state.last_budget is not None
            else None
        )
        if (
            target is None
            or deps.context_state.estimator.messages(projected_history) > target
        ):
            return None
        try:
            raw_reader = getattr(self.store, "get_raw_history", self.store.get_history)
            raw_history = await raw_reader(session_id, agent_id=deps.agent_id)
            event = await self._persist_compaction(
                session_id,
                deps,
                raw_history=raw_history,
                projected_history=projected_history,
                trigger="pressure",
                started_at=(
                    deps.context_state.compaction_started_at
                    or time.perf_counter()
                ),
                target_tokens=target,
            )
            if event is None:
                logger.warning(
                    "Context compaction Event skipped for {}/{}",
                    deps.agent_id,
                    session_id,
                )
                await deps.context_state.finish_compaction(
                    "stale", "source_history_changed"
                )
            return event
        except Exception as exc:  # derived-state failure must not fail a completed chat
            if deps.context_state is not None:
                await deps.context_state.finish_compaction(
                    "failed", type(exc).__name__
                )
            logger.warning(
                "Context compaction Event save failed for {}/{}: {}",
                deps.agent_id,
                session_id,
                type(exc).__name__,
            )
            return False

    async def _persist_compaction(
        self,
        session_id: str,
        deps: AgentDeps,
        *,
        raw_history: list[ModelMessage],
        projected_history: list[ModelMessage],
        trigger: str,
        started_at: float,
        target_tokens: int,
    ):
        persist = getattr(self.store, "persist_compaction_projection", None)
        if not callable(persist):
            return None
        summaries = summary_contents(projected_history)
        if not summaries:
            return None
        preserved_tail = [
            message
            for message in projected_history
            if not summary_contents([message])
        ]
        return await persist(
            session_id,
            agent_id=deps.agent_id,
            user_id=deps.user,
            raw_history=raw_history,
            projected_history=projected_history,
            summary_markdown=summaries[-1],
            trigger=trigger,
            model_id=self._summary_model_id,
            estimated_before=self._estimator.messages(raw_history),
            estimated_after=self._estimator.messages(projected_history),
            target_tokens=target_tokens,
            context_window_tokens=self._context_window_tokens,
            preserved_tail_tokens=self._estimator.messages(preserved_tail),
            duration_ms=max(0, round((time.perf_counter() - started_at) * 1_000)),
            compaction_event_id=(
                deps.context_state.compaction_event_id
                if deps.context_state is not None else None
            ),
            pruned_tool_results=(
                list(deps.context_state.pruned_tool_results)
                if deps.context_state is not None else []
            ),
            summary_call=(
                dict(deps.context_state.summary_call)
                if deps.context_state is not None
                and deps.context_state.summary_call is not None
                else None
            ),
        )

    async def _compaction_after_persist(
        self,
        persister: EventPersister,
        session_id: str,
        deps: AgentDeps,
        *,
        projected_history: list[ModelMessage],
        expected_row_version: int,
    ) -> Any | bool | None:
        try:
            await persister.flush(session_id, agent_id=deps.agent_id)
        except Exception as exc:
            logger.warning(
                "Compaction persistence wait failed for session {}: {}",
                session_id,
                type(exc).__name__,
            )
            return False
        return await self._maybe_save_compaction(
            session_id,
            deps,
            projected_history=projected_history,
            expected_row_version=expected_row_version,
        )

    async def _llm_inputs(
        self,
        history: list[ModelMessage],
        message: str,
        attachments: list[AttachmentRef] | None,
        context_refs: list[ContextReference] | None,
        deps: AgentDeps,
        *,
        pre_persisted_user: bool,
    ) -> tuple[list[ModelMessage], str | list[UserContent] | None]:
        current_user_index = len(history) - 1 if pre_persisted_user else None
        llm_history = await resolve_history_for_llm(
            history,
            current_user_index=current_user_index,
            request_headers=deps.request_headers,
        )
        if pre_persisted_user:
            return llm_history, None
        if has_user_turn(message, attachments, context_refs):
            return llm_history, await resolve_user_prompt(
                message,
                attachments,
                deps.request_headers,
                context_refs=context_refs,
            )
        return llm_history, message or None

    @staticmethod
    def _set_tool_discovery_inputs(
        deps: AgentDeps,
        message: str,
        context_refs: list[ContextReference] | None,
    ) -> None:
        """Keep raw user intent separate from the model-facing context projection."""
        primary = message.strip()
        if primary:
            deps.tool_discovery_inputs_explicit = True
            deps.tool_discovery_primary_query = primary
            deps.tool_discovery_supplemental_query = ""
            return
        if context_refs:
            deps.tool_discovery_inputs_explicit = True
            deps.tool_discovery_primary_query = ""
            deps.tool_discovery_supplemental_query = "\n".join(
                " ".join(filter(None, (ref.type, ref.label or ""))).strip()
                for ref in context_refs
            )
            return
        deps.tool_discovery_inputs_explicit = False
        deps.tool_discovery_primary_query = ""
        deps.tool_discovery_supplemental_query = ""

    async def _persist_run_messages(
        self,
        session_id: str,
        new_messages: list[ModelMessage],
        deps: AgentDeps,
        *,
        pre_persisted_user: bool,
        repair_dangling_tool_calls: bool = True,
        result_queue: asyncio.Queue[dict[str, Any]] | None = None,
        terminal_status: str | None = None,
    ) -> bool:
        to_append = incremental_append_messages(new_messages, skip_user_prompt=pre_persisted_user)
        if repair_dangling_tool_calls:
            to_append = repair_unprocessed_tool_calls(to_append)
        if not to_append:
            return False
        await self.store.append(
            session_id,
            to_append,
            agent_id=deps.agent_id,
            user_id=deps.user,
            app_id=deps.app_id,
            space_id=deps.space_id,
            turn_id=deps.turn_id,
            run_id=deps.run_id,
            first_message_ordinal=1 if pre_persisted_user else 0,
            terminal_status=terminal_status,
        )
        return True

    def _publish_run_complete(
        self,
        persister: EventPersister,
        session_id: str,
        deps: AgentDeps,
        new_messages: list[ModelMessage],
        *,
        pre_persisted_user: bool,
        repair_dangling_tool_calls: bool = True,
        result_queue: asyncio.Queue[dict[str, Any]] | None = None,
        terminal_status: str | None = None,
    ) -> None:
        persister.publish(
            PersistEvent(
                session_id=session_id,
                agent_id=deps.agent_id,
                user_id=deps.user,
                kind="run_complete",
                turn_id=deps.turn_id,
                run_id=deps.run_id,
                app_id=deps.app_id,
                space_id=deps.space_id,
                new_messages=new_messages,
                skip_user_prompt=pre_persisted_user,
                repair_dangling_tool_calls=repair_dangling_tool_calls,
                terminal_status=terminal_status,
                result_queue=result_queue,
            )
        )

    def _publish_run_cancelled(
        self,
        persister: EventPersister,
        session_id: str,
        deps: AgentDeps,
        *,
        pre_persisted_user: bool,
        official_messages: list[ModelMessage],
        result_queue: asyncio.Queue[dict[str, Any]] | None = None,
    ) -> None:
        persister.publish(
            PersistEvent(
                session_id=session_id,
                agent_id=deps.agent_id,
                user_id=deps.user,
                kind="run_cancelled",
                turn_id=deps.turn_id,
                run_id=deps.run_id,
                app_id=deps.app_id,
                space_id=deps.space_id,
                result_messages=official_messages,
                skip_user_prompt=pre_persisted_user,
                repair_dangling_tool_calls=False,
                result_queue=result_queue,
            )
        )


    async def _maybe_generate_title(
        self,
        session_id: str,
        deps: AgentDeps,
        new_messages: list[ModelMessage],
    ) -> None:
        """后台生成并持久化会话标题。"""
        try:
            async with self._session_lock(session_id, agent_id=deps.agent_id):
                history = await self.store.get_history(session_id, agent_id=deps.agent_id)
                user_count = sum(1 for m in history if isinstance(m, ModelRequest))
                if not (user_count <= 1):
                    return

                user_text = first_user_prompt(history) or ""
                existing = await self.store.get_session_title(session_id, agent_id=deps.agent_id)
                # 已有非 seed 标题（AI 或手动）则不再覆盖；seed 与用户首句相同时允许 AI 升级。
                if existing and (not user_text or existing != user_text):
                    return

            assistant_text = _extract_assistant_text(new_messages) or _extract_assistant_text(history)

            from deep_agent.agent.title_generator import generate_title

            title = await generate_title(
                user_text=user_text,
                assistant_text=assistant_text or user_text,
                main_model=self.agent.model,
                auxiliary_store=(
                    self._stores.auxiliary_model if self._stores is not None else None
                ),
                llm_model_store=(
                    self._stores.llm_model if self._stores is not None else None
                ),
                llm_configuration_store=(
                    self._stores.llm_configuration
                    if self._stores is not None
                    else None
                ),
            )

            if not title:
                title = user_text

            if title:
                async with self._session_lock(session_id, agent_id=deps.agent_id):
                    latest = await self.store.get_session_title(session_id, agent_id=deps.agent_id)
                    if latest and (not user_text or latest != user_text):
                        return
                    conditional_update = getattr(
                        self.store, "update_session_title_if_current", None
                    )
                    if callable(conditional_update):
                        await conditional_update(
                            session_id,
                            deps.agent_id,
                            expected_title=latest,
                            title=title,
                        )
                    else:
                        await self.store.update_session_title(
                            session_id, deps.agent_id, title
                        )

        except Exception as e:
            logger.warning("Title generation failed for session {}: {}", session_id, e)

    async def _generate_title_after_persist(
        self,
        persister: EventPersister,
        session_id: str,
        deps: AgentDeps,
        new_messages: list[ModelMessage],
    ) -> None:
        try:
            await persister.flush(session_id, agent_id=deps.agent_id)
        except Exception as exc:  # noqa: BLE001 - background task must not leak failures
            logger.warning("Title persistence wait failed for session {}: {}", session_id, exc)
            return
        await self._maybe_generate_title(session_id, deps, new_messages)

    async def run(
        self,
        session_id: str,
        message: str,
        deps: AgentDeps,
        *,
        attachments: list[AttachmentRef] | None = None,
        context_refs: list[ContextReference] | None = None,
        deferred_tool_results: DeferredToolResults | None = None,
        branch_from: int | None = None,
    ) -> dict[str, Any]:
        self._start_run_identity(session_id, deps)
        await self._ensure_turn_identity(
            session_id, deps, message=message, attachments=attachments,
            context_refs=context_refs, deferred_tool_results=deferred_tool_results,
        )
        await self._prepare_plan_store(session_id, deps)
        self._prepare_skill_runtime(deps)
        started_at = time.perf_counter()
        run_event = await self._start_run_event(session_id, deps, mode="non_stream")
        log_event(
            "agent_run_started",
            conversation_id=deps.conversation_id,
            run_id=deps.run_id,
            agent_id=deps.agent_id,
            session_id=session_id,
            model_id=self._model_id,
            mode="non_stream",
        )
        self._set_tool_discovery_inputs(deps, message, context_refs)
        async with self._session_lock(session_id, agent_id=deps.agent_id):
            try:
                prepared = await self._prepare_history(
                    session_id,
                    message,
                    deps,
                    attachments=attachments,
                    context_refs=context_refs,
                    branch_from=branch_from,
                    deferred_tool_results=deferred_tool_results,
                )
            except BaseException as exc:
                await self._finish_run_event(
                    run_event,
                    status="cancelled" if isinstance(exc, (RunCancelled, asyncio.CancelledError)) else "failed",
                    started_at=started_at,
                    usage=None,
                    error=type(exc).__name__,
                )
                raise
            self._bind_tool_output_store(deps, prepared.raw)
            try:
                llm_history, user_prompt = await self._llm_inputs(
                    prepared.projected,
                    message,
                    attachments,
                    context_refs,
                    deps,
                    pre_persisted_user=prepared.pre_persisted_user,
                )
            except BaseException as exc:
                await self._finish_run_event(
                    run_event,
                    status=(
                        "cancelled"
                        if isinstance(exc, (RunCancelled, asyncio.CancelledError))
                        else "failed"
                    ),
                    started_at=started_at,
                    usage=None,
                    error=type(exc).__name__,
                )
                raise
            self._prepare_context_state(deps, prepared)
            try:
                result = await self.agent.run(
                    user_prompt,
                    deps=deps,
                    message_history=llm_history,
                    usage_limits=self.usage_limits,
                    deferred_tool_results=deferred_tool_results,
                    output_type=_STREAM_OUTPUT_TYPE,
                    model_settings=self.model_settings,
                    conversation_id=deps.conversation_id,
                    run_id=deps.run_id,
                )
            except BaseException as exc:
                await self._finish_run_event(
                    run_event,
                    status="cancelled" if isinstance(exc, (RunCancelled, asyncio.CancelledError)) else "failed",
                    started_at=started_at,
                    usage=getattr(exc, "usage", None),
                    error=type(exc).__name__,
                )
                raise
            new_messages = result.new_messages()
            await self._attach_final_plan_snapshot(new_messages, deps)
            self._record_loaded_skills(deps, result.all_messages())
            await self._persist_run_messages(
                session_id,
                new_messages,
                deps,
                pre_persisted_user=prepared.pre_persisted_user,
                repair_dangling_tool_calls=not isinstance(result.output, DeferredToolRequests),
                terminal_status=(
                    "pending"
                    if isinstance(result.output, DeferredToolRequests)
                    else "completed"
                ),
            )
            compaction_event_saved: bool | None = None
            if not isinstance(result.output, DeferredToolRequests):
                compaction_event = await self._maybe_save_compaction(
                    session_id,
                    deps,
                    projected_history=result.all_messages(),
                    expected_row_version=0,
                )
                compaction_event_saved = (
                    None
                    if compaction_event is None
                    else False
                    if compaction_event is False
                    else True
                )
        response = _build_run_result(result, self.ui_tool_hints)
        response["conversation_id"] = result.conversation_id or deps.conversation_id
        response["run_id"] = result.run_id or deps.run_id
        response["run_metrics"] = run_metrics(
            status=response["status"],
            started_at=started_at,
            model_id=self._model_id,
            usage=result.usage,
            tool_search_calls=(deps.tool_discovery.search_calls if deps.tool_discovery else 0),
            compaction=self._compaction_name(deps),
            cache_metrics_available=cache_usage_available(result.new_messages()),
        )
        response["context"] = self._context_status(prepared.projected, deps)
        response["context"]["compaction"]["compaction_event_saved"] = compaction_event_saved
        terminal_status = (
            "pending" if isinstance(result.output, DeferredToolRequests) else "completed"
        )
        updated_run_event = await self._finish_run_event(
            run_event, status=terminal_status, started_at=started_at, usage=result.usage
        )
        if updated_run_event is not None:
            response["session_event"] = updated_run_event.public_dict()
        log_model_requests(
            result.new_messages(),
            conversation_id=deps.conversation_id,
            run_id=deps.run_id,
            agent_id=deps.agent_id,
            session_id=session_id,
        )
        log_event(
            "agent_run_completed",
            conversation_id=deps.conversation_id,
            run_id=deps.run_id,
            agent_id=deps.agent_id,
            session_id=session_id,
            **response["run_metrics"],
        )
        return response

    async def _stream_agent_events(
        self,
        *,
        user_prompt: str | list[UserContent] | None,
        message_history: list[ModelMessage],
        deferred_tool_results: DeferredToolResults | None,
        deps: AgentDeps,
        cancellation_token: CancellationToken,
        usage: RunUsage,
        session_events: asyncio.Queue[dict[str, Any]],
    ) -> AsyncIterator[tuple[str, Any]]:
        """Interleave Pydantic stream events with DeepAgent lifecycle progress."""

        async with self.agent.run_stream_events(
            user_prompt,
            deps=deps,
            message_history=message_history,
            usage_limits=self.usage_limits,
            deferred_tool_results=deferred_tool_results,
            output_type=_STREAM_OUTPUT_TYPE,
            model_settings=self.model_settings,
            cancellation_token=cancellation_token,
            conversation_id=deps.conversation_id,
            run_id=deps.run_id,
            usage=usage,
        ) as stream:
            stream_task: asyncio.Task[Any] | None = asyncio.create_task(
                stream.__anext__()
            )
            status_task: asyncio.Task[Any] | None = asyncio.create_task(
                session_events.get()
            )
            try:
                while stream_task is not None:
                    waiters = {stream_task}
                    if status_task is not None:
                        waiters.add(status_task)
                    done, _ = await asyncio.wait(
                        waiters,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if status_task is not None and status_task in done:
                        yield "status", status_task.result()
                        status_task = asyncio.create_task(session_events.get())
                    if stream_task not in done:
                        continue
                    try:
                        event = stream_task.result()
                    except StopAsyncIteration:
                        stream_task = None
                        while not session_events.empty():
                            yield "status", session_events.get_nowait()
                        break
                    stream_task = asyncio.create_task(stream.__anext__())
                    yield "model", event
            finally:
                for task in (stream_task, status_task):
                    if task is not None and not task.done():
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task

    async def _recover_provider_overflow(
        self,
        session_id: str,
        deps: AgentDeps,
        *,
        message_history: list[ModelMessage],
        raw_history: list[ModelMessage],
        usage: RunUsage,
    ) -> tuple[list[ModelMessage], Any] | None:
        compaction = self._context_compaction
        state = deps.context_state
        if compaction is None or state is None:
            return None
        request_context = ModelRequestContext(
            model=self.agent.model,
            messages=message_history,
            model_settings=self.model_settings,
            model_request_parameters=ModelRequestParameters(),
        )
        budget = build_context_budget(
            message_history,
            context_window_tokens=self._context_window_tokens,
            max_output_tokens=self._max_output_tokens,
            estimator=self._estimator,
            output_history=raw_history,
            estimate_scope="full_model_request",
        )
        target = budget.target_tokens
        if target is None:
            return None
        run_context = RunContext(
            deps=deps,
            model=self.agent.model,
            usage=usage,
            usage_limits=self.usage_limits,
            agent=self.agent,
            messages=message_history,
            model_settings=self.model_settings,
        )
        started_at = time.perf_counter()
        try:
            projected = await compaction.compact_after_provider_overflow(
                run_context,
                request_context,
                state=state,
                target_tokens=target,
            )
        except RunCancelled:
            await state.finish_compaction("cancelled", "cancelled")
            raise
        except SummaryUnavailable as exc:
            await state.finish_compaction(
                "timed_out" if exc.reason == "summary_timeout" else "failed",
                exc.reason,
            )
            logger.warning(
                "Provider-overflow compaction unavailable for session {}: {}",
                session_id,
                exc.reason,
            )
            return None
        except Exception as exc:  # explicit overflow must fail closed
            await state.finish_compaction("failed", type(exc).__name__)
            logger.warning(
                "Provider-overflow compaction failed for session {}: {}",
                session_id,
                type(exc).__name__,
            )
            return None
        event = await self._persist_compaction(
            session_id,
            deps,
            raw_history=raw_history,
            projected_history=projected,
            trigger="provider_overflow",
            started_at=started_at,
            target_tokens=target,
        )
        if event is None:
            await state.finish_compaction("stale", "source_history_changed")
            logger.warning(
                "Provider-overflow compaction became stale for session {}",
                session_id,
            )
            return None
        return projected, event

    async def run_stream(
        self,
        session_id: str,
        message: str,
        deps: AgentDeps,
        *,
        attachments: list[AttachmentRef] | None = None,
        context_refs: list[ContextReference] | None = None,
        deferred_tool_results: DeferredToolResults | None = None,
        branch_from: int | None = None,
        persister: EventPersister,
        active_run: ActiveRun | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        self._start_run_identity(
            session_id,
            deps,
            run_id=active_run.run_id if active_run is not None else None,
        )
        await self._ensure_turn_identity(
            session_id, deps, message=message, attachments=attachments,
            context_refs=context_refs, deferred_tool_results=deferred_tool_results,
        )
        await self._prepare_plan_store(session_id, deps)
        self._prepare_skill_runtime(deps)
        started_at = time.perf_counter()
        run_event = await self._start_run_event(session_id, deps, mode="stream")
        log_event(
            "agent_run_started",
            conversation_id=deps.conversation_id,
            run_id=deps.run_id,
            agent_id=deps.agent_id,
            session_id=session_id,
            model_id=self._model_id,
            mode="stream",
        )
        compaction_projection: list[ModelMessage] | None = None
        pre_saved_compaction_event: Any | None = None
        cancelled_run: RunCancelled | None = None
        cancelled_messages: list[ModelMessage] = []
        published_tool_calls: dict[str, str] = {}
        session_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._set_tool_discovery_inputs(deps, message, context_refs)
        async with self._session_lock(session_id, agent_id=deps.agent_id):
            try:
                prepared = await self._prepare_history(
                    session_id,
                    message,
                    deps,
                    attachments=attachments,
                    context_refs=context_refs,
                    branch_from=branch_from,
                    deferred_tool_results=deferred_tool_results,
                    session_event_queue=session_events,
                )
            except BaseException as exc:
                failed_run_event = await self._finish_run_event(
                    run_event,
                    status=(
                        "cancelled"
                        if isinstance(exc, (RunCancelled, asyncio.CancelledError))
                        else "failed"
                    ),
                    started_at=started_at,
                    usage=None,
                    error=type(exc).__name__,
                )
                if failed_run_event is not None:
                    yield {
                        "type": "session_event_updated",
                        "persisted": True,
                        "event": failed_run_event.public_dict(),
                    }
                if isinstance(exc, (RunCancelled, asyncio.CancelledError)):
                    raise
                yield events.error(format_stream_error(exc))
                return
            self._bind_tool_output_store(deps, prepared.raw)
            try:
                llm_history, user_prompt = await self._llm_inputs(
                    prepared.projected,
                    message,
                    attachments,
                    context_refs,
                    deps,
                    pre_persisted_user=prepared.pre_persisted_user,
                )
            except Exception as exc:  # attachment/media validation before model start
                logger.warning(
                    "Agent input preparation failed for session {}: {}",
                    session_id,
                    exc,
                )
                failed_run_event = await self._finish_run_event(
                    run_event,
                    status="failed",
                    started_at=started_at,
                    usage=None,
                    error=type(exc).__name__,
                )
                if failed_run_event is not None:
                    yield {
                        "type": "session_event_updated",
                        "persisted": True,
                        "event": failed_run_event.public_dict(),
                    }
                yield events.error(
                    format_stream_error(exc),
                    history_persisted=prepared.pre_persisted_user,
                )
                return
            self._prepare_context_state(
                deps,
                prepared,
                event_queue=session_events,
            )
            result = None
            run_published = False
            active_run = active_run or self._active_runs.register(
                agent_id=deps.agent_id,
                session_id=session_id,
                user_id=deps.user,
                conversation_id=deps.conversation_id,
                run_id=deps.run_id,
            )
            deps.cancellation_token = active_run.cancellation_token
            try:
                if active_run.cancellation_token.cancelled:
                    # The client can cancel immediately after the session SSE,
                    # before Pydantic AI has started a model run. Do not create
                    # background capability/model tasks for an already-cancelled
                    # token; there is no assistant response to recover yet.
                    raise RunCancelled(
                        "Run cancelled before model start",
                        usage=RunUsage(),
                    )
                model_history = llm_history
                model_usage = RunUsage()
                provider_retry_safe = True
                for provider_attempt in range(2):
                    requests_before_attempt = model_usage.requests
                    try:
                        async for source, event in self._stream_agent_events(
                            user_prompt=user_prompt,
                            message_history=model_history,
                            deferred_tool_results=deferred_tool_results,
                            deps=deps,
                            cancellation_token=active_run.cancellation_token,
                            usage=model_usage,
                            session_events=session_events,
                        ):
                            if source == "status":
                                status_event = event
                                if status_event.get("type") == "context_compaction_progress":
                                    log_event(
                                        "context_compaction_phase",
                                        conversation_id=deps.conversation_id,
                                        run_id=deps.run_id,
                                        agent_id=deps.agent_id,
                                        session_id=session_id,
                                        phase=status_event.get("phase"),
                                        mode=status_event.get("mode", "automatic"),
                                    )
                                yield status_event
                                continue
                            if isinstance(event, AgentRunResultEvent):
                                result = event.result
                                continue
                            await self._capture_planning_result(event, deps)
                            mapped = events.map_event(event)
                            if mapped is not None:
                                if not _should_publish_tool_call(event, mapped):
                                    continue
                                provider_retry_safe = False
                                event_type = mapped.get("type")
                                tool_call_id = mapped.get("tool_call_id")
                                if event_type == "tool_call_removed":
                                    if not tool_call_id or tool_call_id not in published_tool_calls:
                                        continue
                                    published_tool_calls.pop(tool_call_id, None)
                                elif event_type == "tool_call" and tool_call_id:
                                    published_tool_calls[tool_call_id] = str(mapped.get("tool") or "tool")
                                elif event_type == "tool_result" and tool_call_id:
                                    published_tool_calls.pop(tool_call_id, None)
                                if event_type in {"tool_call", "tool_result", "tool_call_removed"}:
                                    log_event(
                                        "agent_tool_event",
                                        conversation_id=deps.conversation_id,
                                        run_id=deps.run_id,
                                        agent_id=deps.agent_id,
                                        session_id=session_id,
                                        phase=event_type,
                                        tool=mapped.get("tool"),
                                        tool_call_id=tool_call_id,
                                        source=mapped.get("source") or "tool",
                                    )
                                yield enrich_tool_event(mapped, self.ui_tool_hints)
                        break
                    except Exception as exc:
                        if (
                            provider_attempt != 0
                            or not provider_retry_safe
                            or not is_provider_context_overflow(exc)
                        ):
                            raise
                        if model_usage.requests == requests_before_attempt:
                            model_usage.requests += 1
                        recovered = await self._recover_provider_overflow(
                            session_id,
                            deps,
                            message_history=model_history,
                            raw_history=prepared.raw,
                            usage=model_usage,
                        )
                        while not session_events.empty():
                            yield session_events.get_nowait()
                        if recovered is None:
                            raise
                        model_history, pre_saved_compaction_event = recovered
                        yield {
                            "type": "session_event_updated",
                            "persisted": True,
                            "event": pre_saved_compaction_event.public_dict(),
                        }
                if result is not None:
                    deferred_pending = isinstance(result.output, DeferredToolRequests)
                    new_messages = result.new_messages()
                    await self._attach_final_plan_snapshot(new_messages, deps)
                    self._publish_run_complete(
                        persister,
                        session_id,
                        deps,
                        new_messages,
                        pre_persisted_user=prepared.pre_persisted_user,
                        repair_dangling_tool_calls=not deferred_pending,
                        terminal_status="pending" if deferred_pending else "completed",
                        result_queue=session_events,
                    )
                    run_published = True
                    if not deferred_pending and pre_saved_compaction_event is None:
                        compaction_projection = result.all_messages()

                    if branch_from is None:
                        asyncio.create_task(
                            self._generate_title_after_persist(
                                persister,
                                session_id,
                                deps,
                                new_messages,
                            )
                        )
            except RunCancelled as exc:
                cancelled_run = exc
            except asyncio.CancelledError as exc:
                active_run.cancellation_token.cancel()
                cancelled_run = RunCancelled.from_cancellation(exc)
                raise
            except GeneratorExit:
                active_run.cancellation_token.cancel()
                raise
            except Exception as exc:  # noqa: BLE001 - surface as an SSE error event
                logger.exception("Agent stream failed for session {}", session_id)
                failed_run_event = await self._finish_run_event(
                    run_event, status="failed", started_at=started_at,
                    usage=None, error=type(exc).__name__,
                )
                if failed_run_event is not None:
                    yield {
                        "type": "session_event_updated",
                        "persisted": True,
                        "event": failed_run_event.public_dict(),
                    }
                log_event(
                    "agent_run_failed",
                    conversation_id=deps.conversation_id,
                    run_id=deps.run_id,
                    agent_id=deps.agent_id,
                    session_id=session_id,
                    model_id=self._model_id,
                    status="failed",
                    duration_ms=max(0, round((time.perf_counter() - started_at) * 1_000)),
                    error_type=type(exc).__name__,
                )
                yield events.error(
                    format_stream_error(exc),
                    history_persisted=prepared.pre_persisted_user,
                )
                return
            finally:
                if cancelled_run is not None and not run_published:
                    cancelled_messages = mark_cancelled_response_interrupted(
                        cancelled_run.new_messages()
                    )
                    await self._attach_final_plan_snapshot(cancelled_messages, deps)
                    self._publish_run_cancelled(
                        persister,
                        session_id,
                        deps,
                        pre_persisted_user=prepared.pre_persisted_user,
                        official_messages=cancelled_messages,
                        result_queue=session_events,
                    )
                self._active_runs.unregister(active_run)

        if cancelled_run is not None:
            pending = cancelled_messages
            history_persisted = False
            try:
                # The runner's session lock is released before the persister acquires it.
                await persister.flush(session_id, agent_id=deps.agent_id)
                history_persisted = bool(pending) or prepared.pre_persisted_user
                while not session_events.empty():
                    yield session_events.get_nowait()
            except Exception as exc:  # noqa: BLE001 - surface persistence state in SSE
                logger.exception(
                    "Cancelled history persistence failed for session {}: {}",
                    session_id,
                    exc,
                )
            retained_call_ids = {
                part.tool_call_id
                for message in pending
                if isinstance(message, ModelResponse)
                for part in message.parts
                if isinstance(part, ToolCallPart) and part.tool_call_id
            }
            for tool_call_id, tool_name in list(published_tool_calls.items()):
                if tool_call_id not in retained_call_ids:
                    yield events.tool_call_removed(
                        tool_name,
                        tool_call_id,
                        reason="cancelled",
                    )
            cancelled_run_event = await self._finish_run_event(
                run_event, status="cancelled", started_at=started_at,
                usage=cancelled_run.usage,
            )
            if cancelled_run_event is not None:
                yield {
                    "type": "session_event_updated",
                    "persisted": True,
                    "event": cancelled_run_event.public_dict(),
                }
            yield events.cancelled(
                history_persisted=history_persisted,
                usage=_run_usage_dict(cancelled_run.usage),
                conversation_id=deps.conversation_id,
                run_id=deps.run_id,
                run_metrics=run_metrics(
                    status="cancelled",
                    started_at=started_at,
                    model_id=self._model_id,
                    usage=cancelled_run.usage,
                    tool_search_calls=(
                        deps.tool_discovery.search_calls if deps.tool_discovery else 0
                    ),
                    compaction=self._compaction_name(deps),
                    cache_metrics_available=cache_usage_available(pending),
                ),
            )
            log_model_requests(
                pending,
                conversation_id=deps.conversation_id,
                run_id=deps.run_id,
                agent_id=deps.agent_id,
                session_id=session_id,
            )
            log_event(
                "agent_run_cancelled",
                conversation_id=deps.conversation_id,
                run_id=deps.run_id,
                agent_id=deps.agent_id,
                session_id=session_id,
                **run_metrics(
                    status="cancelled",
                    started_at=started_at,
                    model_id=self._model_id,
                    usage=cancelled_run.usage,
                    tool_search_calls=(
                        deps.tool_discovery.search_calls if deps.tool_discovery else 0
                    ),
                    compaction=self._compaction_name(deps),
                    cache_metrics_available=cache_usage_available(pending),
                ),
            )
            return

        if result is not None:
            self._record_loaded_skills(deps, result.all_messages())
            try:
                await persister.flush(session_id, agent_id=deps.agent_id)
            except Exception as exc:
                failed_run_event = await self._finish_run_event(
                    run_event,
                    status="failed",
                    started_at=started_at,
                    usage=result.usage,
                    error="EventPersistenceFailed",
                )
                if failed_run_event is not None:
                    yield {
                        "type": "session_event_updated",
                        "persisted": True,
                        "event": failed_run_event.public_dict(),
                    }
                yield events.error(f"会话事件持久化失败：{type(exc).__name__}")
                return
            while not session_events.empty():
                yield session_events.get_nowait()
        compaction_event_saved: bool | None = (
            True if pre_saved_compaction_event is not None else None
        )
        if compaction_projection is not None:
            compaction_event = await self._compaction_after_persist(
                persister,
                session_id,
                deps,
                projected_history=compaction_projection,
                expected_row_version=0,
            )
            compaction_event_saved = (
                None
                if compaction_event is None
                else False
                if compaction_event is False
                else True
            )
            while not session_events.empty():
                yield session_events.get_nowait()
            if compaction_event is not None and compaction_event is not False:
                yield {
                    "type": "session_event_updated",
                    "persisted": True,
                    "event": compaction_event.public_dict(),
                }

        final_context = self._context_status(prepared.projected, deps)
        final_context["compaction"]["compaction_event_saved"] = compaction_event_saved
        metrics = run_metrics(
            status=(
                "pending"
                if result is not None and isinstance(result.output, DeferredToolRequests)
                else "completed"
            ),
            started_at=started_at,
            model_id=self._model_id,
            usage=result.usage if result is not None else None,
            tool_search_calls=(deps.tool_discovery.search_calls if deps.tool_discovery else 0),
            compaction=self._compaction_name(deps),
            cache_metrics_available=(
                cache_usage_available(result.new_messages()) if result is not None else False
            ),
        )
        if result is not None:
            log_model_requests(
                result.new_messages(),
                conversation_id=deps.conversation_id,
                run_id=deps.run_id,
                agent_id=deps.agent_id,
                session_id=session_id,
            )
        log_event(
            "agent_run_completed",
            conversation_id=deps.conversation_id,
            run_id=deps.run_id,
            agent_id=deps.agent_id,
            session_id=session_id,
            **metrics,
        )
        terminal_status = (
            "pending"
            if result is not None and isinstance(result.output, DeferredToolRequests)
            else "completed"
        )
        updated_run_event = await self._finish_run_event(
            run_event,
            status=terminal_status,
            started_at=started_at,
            usage=result.usage if result is not None else None,
        )
        if updated_run_event is not None:
            yield {
                "type": "session_event_updated",
                "persisted": True,
                "event": updated_run_event.public_dict(),
            }
        yield _final_event(
            result,
            self.ui_tool_hints,
            context=final_context,
            conversation_id=deps.conversation_id,
            run_id=deps.run_id,
            run_metrics=metrics,
        )


def _build_run_result(result: Any, ui_tool_hints: dict[str, dict[str, Any]]) -> dict[str, Any]:
    output = result.output
    if isinstance(output, DeferredToolRequests):
        deferred = serialize_deferred(output)
        return {
            "output": None,
            "status": "pending",
            "deferred": enrich_deferred(deferred, ui_tool_hints),
            "usage": _usage_dict(result),
        }
    return {
        "output": _serialize_output(output),
        "status": "completed",
        "usage": _usage_dict(result),
    }


def _final_event(
    result: Any,
    ui_tool_hints: dict[str, dict[str, Any]],
    *,
    context: dict[str, Any] | None = None,
    conversation_id: str | None = None,
    run_id: str | None = None,
    run_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output = result.output
    usage = _usage_dict(result)
    if isinstance(output, DeferredToolRequests):
        deferred = enrich_deferred(serialize_deferred(output), ui_tool_hints)
        return events.final(
            None,
            usage,
            status="pending",
            deferred=deferred,
            context=context,
            conversation_id=conversation_id,
            run_id=run_id,
            run_metrics=run_metrics,
        )
    return events.final(
        _serialize_output(output),
        usage,
        status="completed",
        context=context,
        conversation_id=conversation_id,
        run_id=run_id,
        run_metrics=run_metrics,
    )


def _serialize_output(output: Any) -> Any:
    if isinstance(output, (str, int, float, bool)) or output is None:
        return output
    if hasattr(output, "model_dump"):
        return output.model_dump()
    return str(output)


def _usage_dict(result: Any) -> dict[str, Any]:
    return usage_dict(result.usage)


def _run_usage_dict(usage: RunUsage | None) -> dict[str, Any]:
    return usage_dict(usage)


def _manual_compaction_result(
    status_name: str,
    reason: str | None,
    *,
    usage: RunUsage | None = None,
    telemetry: dict[str, Any] | None = None,
    target_tokens: int | None = None,
    compaction_event: dict[str, Any] | None = None,
    conversation_id: str | None = None,
    run_id: str | None = None,
    run_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    telemetry = dict(telemetry or {})
    return {
        "type": "context_compaction_result",
        "status": status_name,
        "reason": reason,
        "before_tokens": telemetry.get("estimated_tokens_before", 0),
        "after_tokens": telemetry.get("estimated_tokens_after", 0),
        "target_tokens": target_tokens,
        "reduction_ratio": telemetry.get("reduction_ratio", 0.0),
        "summary_calls": telemetry.get("summary_calls", 0),
        "summary_chunks": telemetry.get("summary_chunks", 0),
        "usage": _run_usage_dict(usage),
        "compaction_event": compaction_event,
        "conversation_id": conversation_id,
        "run_id": run_id,
        "run_metrics": run_metrics,
    }
