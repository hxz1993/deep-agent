from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from contextlib import aclosing, asynccontextmanager, suppress
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator

import anyio

from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    CancellationToken,
    DeferredToolRequests,
    DeferredToolResults,
    RunContext,
)
from pydantic_ai.exceptions import RunCancelled, UnexpectedModelBehavior, ModelHTTPError, UsageLimitExceeded
from pydantic_ai.providers.openai_codex import CredentialsRefreshError, CredentialsPersistenceError
from deep_agent.services.codex_provider import CodexUnavailable
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
from deep_agent.agent.message_persistence import MessagePersistenceCapability, StreamMessagePersistenceCapability, MessagePersistenceError
from deep_agent.db.run_ownership import live_run
from deep_agent.agent.limits import format_stream_error
from deep_agent.agent.request_budget import TurnRequestBudget, restored_request_count
from deep_agent.agent.observability import (
    cache_usage_available,
    log_event,
    run_metrics,
    usage_dict,
)
from deep_agent.agent.planning import (
    attach_known_plan_snapshots,
    attach_plan_snapshot,
    current_plan_snapshot,
    is_planning_tool,
    plan_snapshot_from_part,
    prepare_plan_store,
    restore_plan_snapshot,
)
from deep_agent.agent.run_identity import conversation_id_for, new_run_id
from deep_agent.agent.tool_output_limits import PlatformToolResultStore
from deep_agent.agent.history import (
    incremental_append_messages,
    mark_cancelled_response_interrupted,
    repair_unprocessed_tool_calls,
)
from deep_agent.capabilities.ui.registry import enrich_deferred, enrich_tool_event
from deep_agent.capabilities.display_names import (
    enrich_deferred_display_names,
    enrich_tool_event_display_name,
)
from loguru import logger

from deep_agent.db.conversation import SessionStore, _common_message_suffix
from deep_agent.time import utc_rfc3339
from deep_agent.db.serialization import serialize_message
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.schemas.context_reference import ContextReference
from deep_agent.options import OPTION

_STREAM_OUTPUT_TYPE: list[Any] = [str, DeferredToolRequests]
_TERMINAL_WRITE_TIMEOUT = 10.0


def _safe_run_failure(exc: Exception) -> dict[str, Any]:
    """Durable diagnostics contain categories, never raw exception/argument text."""
    error_type = type(exc).__name__
    diagnostic: dict[str, Any] = {
        "code": "agent_run_failed",
        "error_type": error_type if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", error_type) else "Exception",
        "message": f"执行发生 {error_type if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', error_type) else 'Exception'}，本轮已停止。",
        "phase": "agent",
    }
    if isinstance(exc, MessagePersistenceError):
        diagnostic.update(code="message_persistence_failed", phase="persistence",
                          message="已完成内容写入失败，本轮已停止；不能确认本次写入已保存。")
    elif isinstance(exc, TimeoutError):
        diagnostic.update(code="execution_timeout", phase="execution",
                          message="执行等待超时，本轮已停止。")
    elif isinstance(exc, ModelHTTPError):
        diagnostic.update(code="provider_http_error", phase="provider",
                          message=f"模型服务返回 HTTP {exc.status_code}，本轮已停止。")
    elif isinstance(exc, CredentialsRefreshError):
        diagnostic.update(code="provider_authorization_expired", phase="provider",
                          message="订阅授权失效，请在供应商配置中重新授权。")
    elif isinstance(exc, CredentialsPersistenceError):
        diagnostic.update(code="provider_credential_save_failed", phase="provider",
                          message="订阅凭据刷新后保存失败，本轮已停止。")
    elif isinstance(exc, CodexUnavailable):
        diagnostic.update(code="provider_authorization_unavailable", phase="provider",
                          message="订阅当前不可用，请检查开关或重新授权。")
    elif isinstance(exc, UsageLimitExceeded):
        diagnostic.update(code="usage_limit_exceeded", phase="model",
                          message="模型请求或用量达到执行上限，本轮已停止。")
    if isinstance(exc, UnexpectedModelBehavior):
        diagnostic.update(code="unexpected_model_behavior", phase="model",
                          message="模型返回未满足执行协议，本轮已停止。")
        match = re.match(
            r"Tool '([A-Za-z0-9_.:-]{1,200})' exceeded max retries count of ([0-9]{1,6})\.",
            str(exc),
        )
        if match:
            diagnostic.update(
                code="tool_retry_exhausted",
                tool=match.group(1),
                max_retries=int(match.group(2)),
                phase="tool_validation",
                message=f"工具 {match.group(1)} 参数纠错次数已达上限（{match.group(2)} 次），本轮已停止；可以补充要求后继续。",
            )
    return diagnostic


def _with_failed_response_diagnostic(
    messages: list[ModelMessage], diagnostic: dict[str, Any],
) -> list[ModelMessage]:
    result = list(messages)
    for index in range(len(result) - 1, -1, -1):
        message = result[index]
        if isinstance(message, ModelResponse):
            result[index] = replace(message, metadata={**(message.metadata or {}), "deepagent_error": diagnostic})
            break
    return result


@dataclass(frozen=True)
class _PreparedHistory:
    projected: list[ModelMessage]
    raw: list[ModelMessage]
    pre_persisted_user: bool
    user_event_id: str | None = None


@dataclass(frozen=True)
class _RunTerminalContext:
    session_id: str
    deps: AgentDeps
    mode: str


@dataclass
class _StreamRunSnapshot:
    """Messages and usage retained from a live Pydantic AI event stream."""

    new_messages: list[ModelMessage] = field(default_factory=list)
    all_messages: list[ModelMessage] = field(default_factory=list)
    usage: RunUsage | None = None

    def capture(self, stream: Any) -> None:
        # Accessors become available after the first event starts the run. A
        # failure before model start is intentionally represented by an empty
        # snapshot and retains the existing user-only history behavior.
        with suppress(Exception):
            self.new_messages = list(stream.new_messages())
        with suppress(Exception):
            self.all_messages = list(stream.all_messages())
        with suppress(Exception):
            self.usage = deepcopy(stream.usage)


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
        tool_display_names: dict[str, str] | None = None,
        model_settings: ModelSettings | None = None,
        context_window_tokens: int | None = None,
        max_output_tokens: int | None = None,
        model_tags: list[str] | None = None,
        context_compaction: DeepAgentContextCompaction | None = None,
        model_id: str | None = None,
        model_name: str | None = None,
        agent_name: str | None = None,
        provider_id: str | None = None,
        provider_type: str | None = None,
        estimator: TokenEstimator | None = None,
        active_runs: ActiveRunRegistry | None = None,
        skill_runtime_context: dict[str, Any] | None = None,
        tool_output_store: PlatformToolResultStore | None = None,
    ) -> None:
        self.agent = agent
        self.store = store
        self.usage_limits = usage_limits
        self.ui_tool_hints = ui_tool_hints or {}
        self.tool_display_names = tool_display_names or {}
        self.model_settings = model_settings
        self._context_window_tokens = context_window_tokens
        self._max_output_tokens = max_output_tokens
        self._model_tags = list(model_tags or [])
        self._context_compaction = context_compaction
        self._model_id = model_id
        self._model_name = model_name
        self.agent_name = agent_name
        self._provider_id = provider_id
        self._provider_type = provider_type
        self._estimator = estimator or TokenEstimator.conservative()
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

    def _log_run_terminal(
        self, run_event: _RunTerminalContext, *, status: str, started_at: float,
        error: BaseException | None = None, phase: str | None = None,
        usage: RunUsage | None = None,
        reason: str | None = None,
    ) -> None:
        """Observe a terminal branch without participating in its persistence."""
        try:
            deps = run_event.deps
            metrics = {}
            if usage is not None:
                try:
                    metrics = run_metrics(
                        status=status, started_at=started_at, model_id=self._model_id, usage=usage,
                        tool_search_calls=(deps.tool_discovery.search_calls if deps.tool_discovery else 0),
                        compaction=self._compaction_name(deps),
                    )
                except Exception:
                    pass
            metrics.update(status=status, model_id=self._model_id,
                           duration_ms=max(0, round((time.perf_counter() - started_at) * 1_000)))
            log_event(
                f"agent_run_{status}", session_id=run_event.session_id,
                conversation_id=deps.conversation_id, run_id=deps.run_id,
                agent_id=deps.agent_id, agent_name=deps.agent_name, model_name=self._model_name,
                mode=run_event.mode, phase=phase, reason=reason, **metrics,
                error_type=type(error).__name__ if error is not None else None,
            )
        except Exception:
            pass

    async def _finish_run_event(
        self,
        run_event: _RunTerminalContext,
        *,
        status: str,
        started_at: float,
        usage: RunUsage | None,
        error: str | None = None,
        exception: BaseException | None = None,
        phase: str | None = None,
    ):
        if status not in {"failed", "interrupted"}:
            return None
        deps = run_event.deps
        append_error = getattr(self.store, "append_execution_error", None)
        if not callable(append_error):
            return None
        payload = {
            "code": error or ("execution_interrupted" if status == "interrupted" else "agent_run_failed"),
            "message": "执行意外中断，已保留已完成的内容。" if status == "interrupted" else "本轮执行失败，请查看异常分类和服务日志。",
            "phase": "agent",
        }
        if isinstance(exception, Exception):
            payload = _safe_run_failure(exception)
        if phase:
            payload["phase"] = phase
        try:
            async with asyncio.timeout(_TERMINAL_WRITE_TIMEOUT):
                return await append_error(
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
        except Exception as exc:
            # A second DB failure must not hide the original error SSE or
            # prevent the active-run registry from being released.
            logger.error("Execution error persistence failed session={} error={}",
                         run_event.session_id, type(exc).__name__)
            return None

    @staticmethod
    async def _capture_planning_result(event: Any, deps: AgentDeps) -> None:
        """Attach a confirmed Store snapshot to an accepted Planning result."""

        if not isinstance(event, FunctionToolResultEvent):
            return
        part = event.part
        if not isinstance(part, ToolReturnPart) or not is_planning_tool(part.tool_name):
            return
        snapshot = plan_snapshot_from_part(part)
        if snapshot is None:
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
    async def _session_lock(self, session_id: str, *, agent_id: str | None,
                            run_id: str | None = None) -> AsyncIterator[None]:
        lock = getattr(self.store, "lock", None)
        with live_run(run_id):
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
        *, run_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Force one persisted compaction Event without appending a chat turn."""

        deps.agent_name = self.agent_name
        self._start_run_identity(session_id, deps, run_id=run_id)
        self._prepare_skill_runtime(deps)
        started_at = time.perf_counter()

        def manual_result(
            status_name: str,
            reason: str | None,
            *,
            usage: RunUsage | None = None,
            telemetry: dict[str, Any] | None = None,
            budget_limit_tokens: int | None = None,
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
                budget_limit_tokens=budget_limit_tokens,
                compaction_event=compaction_event,
                conversation_id=deps.conversation_id,
                run_id=deps.run_id,
                run_metrics=metrics,
            )
            log_event(
                "manual_compaction_completed",
                conversation_id=deps.conversation_id,
                run_id=deps.run_id,
                agent_id=deps.agent_id, agent_name=deps.agent_name, model_name=self._model_name,
                session_id=session_id,
                reason=reason,
                **metrics,
            )
            return payload

        log_event(
            "manual_compaction_started",
            conversation_id=deps.conversation_id,
            run_id=deps.run_id,
            agent_id=deps.agent_id, agent_name=deps.agent_name, model_name=self._model_name,
            session_id=session_id,
            model_id=self._model_id,
        )

        if not self.manual_compaction_enabled:
            yield manual_result("failed", "context_window_unknown")
            return
        assert self._context_compaction is not None

        async with self._session_lock(session_id, agent_id=deps.agent_id, run_id=deps.run_id):
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
            budget_limit_tokens = budget.usable_request_tokens
            assert budget_limit_tokens is not None
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
            self._bind_compaction_lifecycle(session_id=session_id, deps=deps,
                                            source_history=visible, model_history=projected)
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
                    budget_limit_tokens=budget_limit_tokens,
                )
            )
            queue_task: asyncio.Task[Any] | None = asyncio.create_task(event_queue.get())
            try:
                while not task.done():
                    if deps.cancellation_token is not None and deps.cancellation_token.cancelled:
                        raise RunCancelled("Manual compaction cancelled", usage=usage)
                    done, _ = await asyncio.wait(
                        {task, queue_task},
                        timeout=0.1 if deps.cancellation_token is not None else None,
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
                state.telemetry.summary_failed = True
                state.telemetry.failure = reason
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
                    budget_limit_tokens=budget_limit_tokens,
                )
                return
            except (asyncio.CancelledError, GeneratorExit, RunCancelled) as exc:
                with anyio.CancelScope(shield=True):
                    task.cancel()
                    with suppress(asyncio.CancelledError, SummaryUnavailable, RunCancelled):
                        await task
                    await state.finish_compaction("cancelled", "cancelled")
                if isinstance(exc, RunCancelled):
                    yield manual_result("cancelled", "cancelled", usage=exc.usage)
                    return
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
                    budget_limit_tokens=budget_limit_tokens,
                )
                return
            finally:
                if not task.done():
                    task.cancel()
                    with anyio.CancelScope(shield=True):
                        with suppress(asyncio.CancelledError):
                            await task
                if queue_task is not None and not queue_task.done():
                    queue_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await queue_task

            try:
                current_raw = await raw_reader(session_id, agent_id=deps.agent_id)
            except asyncio.CancelledError:
                with anyio.CancelScope(shield=True):
                    await state.finish_compaction("cancelled", "cancelled")
                raise
            if _history_hash(current_raw) != original_hash:
                await state.finish_compaction("stale", "source_history_changed")
                while not event_queue.empty():
                    yield event_queue.get_nowait()
                yield manual_result(
                    "failed",
                    "session_changed",
                    usage=usage,
                    telemetry=state.telemetry.as_dict(),
                    budget_limit_tokens=budget_limit_tokens,
                )
                return
            try:
                yield {
                    "type": "context_compaction_progress",
                    "persisted": False,
                    "event_id": state.compaction_event_id,
                    "phase": "committing",
                    "mode": "manual",
                    "message": "正在保存压缩事件…",
                }
                event = await self._persist_compaction(
                    session_id, deps, raw_history=raw, projected_history=compacted,
                    trigger="manual",
                    started_at=state.compaction_started_at or started_at,
                    budget_limit_tokens=budget_limit_tokens,
                )
            except (asyncio.CancelledError, GeneratorExit):
                with anyio.CancelScope(shield=True):
                    await state.finish_compaction("cancelled", "cancelled")
                raise
            except Exception as exc:
                error_code = exc.reason if isinstance(exc, SummaryUnavailable) else type(exc).__name__
                state.telemetry.estimated_tokens_after = state.telemetry.estimated_tokens_before
                state.telemetry.summary_applied = False
                state.telemetry.candidate_valid = False
                state.telemetry.reduction_ratio = 0.0
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
                    budget_limit_tokens=budget_limit_tokens,
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
                    budget_limit_tokens=budget_limit_tokens,
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
                budget_limit_tokens=budget_limit_tokens,
                compaction_event={
                    "event_id": event.event_id,
                    "through_sequence": event.payload["source"]["through_sequence"],
                    "projected_estimated_tokens": event.payload["budget"]["estimated_after"],
                    "summary_model_id": self._model_id,
                    "updated_at": utc_rfc3339(event.updated_at),
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
            repaired = repair_unprocessed_tool_calls(history, diagnostic_fields={
                "session_id": session_id, "run_id": deps.run_id, "agent_id": deps.agent_id,
                "phase": "history_prepare",
            })
            if repaired is not history:
                history = repaired
        pre_persisted_user = False
        user_event_id: str | None = None
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
            if rows:
                user_event_id = rows[0].event_id
            history = [*history, user_request]
            raw_history = [*raw_history, user_request]
            pre_persisted_user = True
        # Compaction changes model-visible history, never attachment ownership.
        # Only original session references (plus this turn's uploads) authorize files.
        deps.attachment_by_id = index_attachment_refs(
            attachments=attachments,
            messages=raw_history,
        )
        deps.has_context_references = history_has_context_references(history)
        return _PreparedHistory(
            projected=history,
            raw=raw_history,
            pre_persisted_user=pre_persisted_user,
            user_event_id=user_event_id,
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
        model_history: list[ModelMessage] | None = None,
        event_queue: asyncio.Queue[dict[str, Any]] | None = None,
    ) -> None:
        if self._context_compaction is None:
            deps.context_state = None
            return
        deps.context_state = ContextRunState(
            raw_history=list(prepared.raw),
            estimator=self._estimator,
            usage_limits=self._run_limits(deps),
            event_queue=event_queue,
        )
        self._bind_compaction_lifecycle(session_id=deps.session_id, deps=deps,
                                       source_history=prepared.projected, model_history=model_history)

    async def _prepare_request_budget(self, session_id: str, deps: AgentDeps) -> None:
        previous_requests = 0
        if deps.turn_id and deps.turn_id != deps.run_id:
            # Turn-scoped raw facts survive compaction and process restarts.
            rows = []
            after = 0
            while True:
                page = await self.store.list_events(
                    session_id, turn_id=deps.turn_id, after_sequence=after, limit=500,
                )
                rows.extend(page)
                if len(page) < 500:
                    break
                after = page[-1].sequence
            previous_requests = restored_request_count(rows)
        deps.request_budget = TurnRequestBudget(self.usage_limits, previous_requests)

    def _run_limits(self, deps: AgentDeps) -> UsageLimits:
        return deps.request_budget.run_limits if deps.request_budget is not None else self.usage_limits

    @staticmethod
    def _run_capabilities(deps: AgentDeps, progress):
        return [capability for capability in (progress, deps.request_budget) if capability is not None]

    def _bind_compaction_lifecycle(
        self, *, session_id: str, deps: AgentDeps,
        source_history: list[ModelMessage] | None = None,
        model_history: list[ModelMessage] | None = None,
    ) -> None:
        state = deps.context_state
        if state is None:
            return
        # Preserve the already resolved input mapping (attachments/context refs)
        # without downloading media again or trusting new source messages.
        input_projection = (deepcopy([
            (source, resolved)
            for source, resolved in zip(source_history or [], model_history, strict=True)
            if _common_message_suffix([source], [resolved]) != 1
        ]) if model_history is not None else [])

        async def refresh_raw_history(request_messages=None):
            reader = getattr(self.store, "get_raw_history", self.store.get_history)
            raw = await reader(session_id, agent_id=deps.agent_id)
            if request_messages is not None:
                visible = await self.store.get_history(session_id, agent_id=deps.agent_id)
                normalized = [next((resolved for source, resolved in input_projection
                                    if _common_message_suffix([source], [message]) == 1), message)
                              for message in visible]
                # A checkpoint anchor is re-rendered on every read, so its
                # synthetic UserPrompt timestamp is not an original Event fact.
                # All content, evidence and protected context still compare.
                if (normalized and request_messages
                        and summary_contents([normalized[0]])
                        and summary_contents([request_messages[0]])):
                    normalized[0] = replace(normalized[0], parts=[
                        replace(normalized[0].parts[0], timestamp=request_messages[0].parts[0].timestamp)
                    ])
                if (len(normalized) != len(request_messages)
                        or _common_message_suffix(normalized, request_messages) != len(normalized)):
                    raise SummaryUnavailable("source_history_changed")
            # Read raw before visible: writes between these reads either fail
            # this full comparison or the atomic raw comparison at commit.
            return raw

        state.refresh_raw_history = refresh_raw_history

        async def commit_projection(projected):
            # Manual and overflow callers persist immediately themselves.
            if state.compaction_trigger != "pressure":
                return projected
            # This is the snapshot used to plan and summarize the prefix. A
            # fresh read here would silently include late, unsummarized Events;
            # the store compares this snapshot with its current rows atomically.
            raw = list(state.raw_history)
            event = await self._persist_compaction(
                session_id, deps, raw_history=raw, projected_history=projected,
                trigger="pressure", started_at=state.compaction_started_at or time.perf_counter(),
                budget_limit_tokens=state.compaction_budget_limit_tokens,
            )
            if event is None:
                raise SummaryUnavailable("source_history_changed")
            state.committed_event = event
            if state.event_queue is not None:
                state.event_queue.put_nowait({"type": "session_event_updated", "persisted": True,
                                             "event": event.public_dict()})
            return await self.store.get_history(session_id, agent_id=deps.agent_id)

        state.commit_projection = commit_projection
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
                "budget": {
                    **state.telemetry.as_dict(),
                    "budget_limit_tokens": state.compaction_budget_limit_tokens,
                },
                "summary_call": state.summary_call,
            }
            return await self.store.update_lifecycle(
                event_id, status=status, payload=terminal_payload
            )

        async def persist_summary_usage(calls):
            if state.compaction_event_id is not None:
                for call in calls:
                    call["model_id"] = self._model_id
                    call["provider_id"] = self._provider_id
                return await self.store.attach_usage(state.compaction_event_id,
                    calls=calls, usage_model_id=self._model_id)

        state.start_compaction_event = start
        state.finish_compaction_event = finish
        state.persist_summary_usage = persist_summary_usage

    async def _maybe_save_compaction(
        self,
        session_id: str,
        deps: AgentDeps,
        *,
        projected_history: list[ModelMessage],
        expected_row_version: int = 0,
    ) -> Any | bool | None:
        del expected_row_version
        if deps.context_state is not None and deps.context_state.committed_event is not None:
            return deps.context_state.committed_event
        if (
            self._context_compaction is None
            or deps.context_state is None
            or not deps.context_state.telemetry.summary_applied
            or deps.context_state.telemetry.summary_failed
            or not deps.context_state.telemetry.candidate_valid
            or deps.context_state.telemetry.strategy == "original_history_fallback"
        ):
            return None
        target = (
            deps.context_state.compaction_budget_limit_tokens
            if deps.context_state.compaction_budget_limit_tokens is not None
            else deps.context_state.last_budget.usable_request_tokens if deps.context_state.last_budget is not None
            else None
        )
        if target is None:
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
                budget_limit_tokens=target,
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
        budget_limit_tokens: int,
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
        state = deps.context_state
        parameters = state.compaction_request_parameters if state is not None else None
        before = (state.compaction_estimated_tokens_before
                  if state is not None and state.compaction_estimated_tokens_before is not None
                  else state.telemetry.estimated_tokens_before if state is not None
                  else self._estimator.estimate_request(raw_history, parameters).tokens)

        def validate_persisted_projection(candidate: list[ModelMessage]) -> tuple[int, int]:
            tokens = self._estimator.messages(candidate)
            request_tokens = self._estimator.estimate_request(candidate, parameters).tokens
            if state is not None:
                state.telemetry.candidate_tokens = request_tokens
            if request_tokens > budget_limit_tokens:
                raise SummaryUnavailable("persisted_request_budget_exceeded")
            if request_tokens >= before:
                raise SummaryUnavailable("persisted_no_reduction")
            if state is not None:
                state.telemetry.estimated_tokens_after = request_tokens
                state.telemetry.reduction_ratio = max(0.0, 1 - request_tokens / max(1, before))
            return tokens, request_tokens

        return await persist(
            session_id,
            agent_id=deps.agent_id,
            user_id=deps.user,
            raw_history=raw_history,
            projected_history=projected_history,
            summary_markdown=summaries[-1],
            trigger=trigger,
            model_id=self._model_id,
            estimated_before=self._estimator.messages(raw_history),
            estimated_after=self._estimator.messages(projected_history),
            estimated_tokens_before=before,
            estimated_tokens_after=(state.telemetry.estimated_tokens_after if state is not None else None),
            estimate_scope=state.compaction_estimate_scope if state is not None else "history_only",
            budget_limit_tokens=budget_limit_tokens,
            validate_projection=validate_persisted_projection,
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
            file_store=deps.file_store,
        )
        if pre_persisted_user:
            return llm_history, None
        if has_user_turn(message, attachments, context_refs):
            return llm_history, await resolve_user_prompt(
                message,
                attachments,
                deps.request_headers,
                context_refs=context_refs,
                file_store=deps.file_store,
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
        dangling_tool_message: str | None = None,
        result_queue: asyncio.Queue[dict[str, Any]] | None = None,
        terminal_status: str | None = None,
    ) -> bool:
        to_append = incremental_append_messages(new_messages, skip_user_prompt=pre_persisted_user)
        if repair_dangling_tool_calls or (terminal_status == "interrupted" and deps.ptc_diagnostics):
            repair_kwargs = (
                {"failure_message" if terminal_status == "failed" else "denied_message": dangling_tool_message}
                if dangling_tool_message is not None
                else {}
            )
            ptc_return_metadata = {
                call_id: {"code_mode": True, "ptc_diagnostics": diagnostic}
                for call_id, diagnostic in deps.ptc_diagnostics.items()
            }
            to_append = repair_unprocessed_tool_calls(to_append, **repair_kwargs, tool_return_metadata=ptc_return_metadata, diagnostic_fields={
                "session_id": session_id, "run_id": deps.run_id, "agent_id": deps.agent_id,
                "phase": "terminal_persistence",
            })
        if not to_append and terminal_status is None:
            return False
        try:
            async with asyncio.timeout(_TERMINAL_WRITE_TIMEOUT):
                rows = await self.store.append(
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
                    usage_model_id=self._model_id,
                    usage_provider_id=self._provider_id,
                )
        except Exception as exc:
            log_event("message_persistence_failed", session_id=session_id,
                      run_id=deps.run_id, agent_id=deps.agent_id, agent_name=deps.agent_name, model_name=self._model_name,
                      phase="terminal", error_type=type(exc).__name__)
            raise
        if result_queue is not None:
            for row in rows or []:
                result_queue.put_nowait({
                    "type": "session_event_appended", "persisted": True,
                    "event": row.public_dict(),
                })
        return True

    async def _close_saved_messages(self, session_id: str, deps: AgentDeps, *, status: str) -> bool:
        """Close statuses on real persisted messages without snapshots or replay."""
        append = getattr(self.store, "append_message_parts", None)
        if not callable(append):
            return False
        try:
            async with asyncio.timeout(_TERMINAL_WRITE_TIMEOUT):
                await append(
                    session_id, agent_id=deps.agent_id, user_id=deps.user,
                    turn_id=deps.turn_id, run_id=deps.run_id, messages=[],
                    terminal_status=status,
                )
            return True
        except Exception as exc:
            log_event("message_persistence_failed", session_id=session_id,
                      run_id=deps.run_id, agent_id=deps.agent_id, agent_name=deps.agent_name, model_name=self._model_name,
                      phase="status_finalization", error_type=type(exc).__name__)
            return False

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


    async def _start_title_generation(
        self,
        session_id: str,
        deps: AgentDeps,
        prepared: _PreparedHistory,
        *,
        message: str,
        attachments: list[AttachmentRef] | None,
        context_refs: list[ContextReference] | None,
        session_event_queue: asyncio.Queue[dict[str, Any]] | None = None,
    ) -> asyncio.Task[None] | None:
        """Persist the fallback once, then start one independent model request."""

        if prepared.user_event_id is None:
            return None
        from deep_agent.agent.title_generator import fallback_title

        details = {
            "context_count": len(context_refs or ()),
            "attachment_count": len(attachments or ()),
            "image_used": False,
        }
        try:
            fallback = await self.store.ensure_title_fallback(
                session_id,
                agent_id=deps.agent_id,
                user_id=deps.user,
                title=fallback_title(message, context_refs, attachments),
                source_event_id=prepared.user_event_id,
                turn_id=deps.turn_id,
                run_id=deps.run_id,
                details=details,
            )
        except Exception as exc:  # noqa: BLE001 - title must never fail the chat run
            logger.warning(
                "Fallback title persistence failed for session {}: {}",
                session_id,
                type(exc).__name__,
            )
            return None
        if fallback is None:
            return None
        if session_event_queue is not None:
            self._publish_title_events(session_event_queue, fallback)
        return asyncio.create_task(
            self._generate_model_title(
                session_id,
                deps,
                fallback_event=fallback,
                message=message,
                attachments=attachments,
                context_refs=context_refs,
                session_event_queue=session_event_queue,
            ),
            name=f"session-title:{session_id}",
        )

    async def _generate_model_title(
        self,
        session_id: str,
        deps: AgentDeps,
        *,
        fallback_event: Any,
        message: str,
        attachments: list[AttachmentRef] | None,
        context_refs: list[ContextReference] | None,
        session_event_queue: asyncio.Queue[dict[str, Any]] | None,
    ) -> None:
        from deep_agent.agent.title_generator import generate_title
        from deep_agent.usage import merge_calls, response_call

        responses = []
        def title_calls():
            return [response_call(response,
                request_id=f"{fallback_event.event_id}:title:{index}",
                model_id=self._model_id, provider_id=self._provider_id, purpose="title")
                for index, response in enumerate(responses)]
        async def retain_on_fallback():
            calls = title_calls()
            if calls:
                await self.store.attach_usage(fallback_event.event_id,
                    calls=calls, usage_model_id=self._model_id)

        try:
            result = await generate_title(
                model=self.agent.model,
                provider_type=self._provider_type,
                diagnostic_fields={
                    "session_id": session_id,
                    "run_id": deps.run_id,
                    "agent_id": deps.agent_id,
                    "model_id": self._model_id,
                    "model_name": self._model_name,
                    "provider_name": self._provider_type,
                },
                user_text=message,
                context_refs=context_refs,
                attachments=attachments,
                file_store=deps.file_store,
                request_headers=deps.request_headers,
                usage_collector=responses,
            )
            if result is None:
                await retain_on_fallback()
                return
            calls = title_calls()
            event = await self.store.apply_session_title(
                session_id,
                agent_id=deps.agent_id,
                user_id=deps.user,
                title=result.title,
                source="model",
                source_event_id=fallback_event.payload.get("source_event_id"),
                turn_id=deps.turn_id,
                run_id=deps.run_id,
                expected_event_id=fallback_event.event_id,
                usage=merge_calls(None, calls),
                usage_model_id=self._model_id if calls else None,
                details={
                    "provider_id": self._provider_id,
                    "provider_type": self._provider_type,
                    "model_id": self._model_id,
                    "model_name": self._model_name,
                    "context_count": len(context_refs or ()),
                    "attachment_count": len(attachments or ()),
                    "image_used": result.image_used,
                },
            )
            if event is None:
                await retain_on_fallback()
        except asyncio.CancelledError:
            # The title task is cancelled when the owning chat closes. A model
            # response already received still consumed tokens, even though its
            # title will never be applied.
            try:
                await asyncio.shield(retain_on_fallback())
            except Exception:
                logger.warning("Title usage persistence failed for session {}", session_id)
            raise
        except Exception as exc:  # noqa: BLE001 - fallback remains authoritative
            try:
                await retain_on_fallback()
            except Exception:
                logger.warning("Title usage persistence failed for session {}", session_id)
            log_event("title_generation_failed", session_id=session_id, run_id=deps.run_id,
                      agent_id=deps.agent_id, agent_name=deps.agent_name, model_name=self._model_name, model_id=self._model_id,
                      phase="title_update", error_type=type(exc).__name__)
            logger.warning(
                "Model title persistence failed for session {}: {}",
                session_id,
                type(exc).__name__,
            )
            return
        if event is not None and session_event_queue is not None:
            self._publish_title_events(session_event_queue, event)

    @staticmethod
    def _publish_title_events(
        queue: asyncio.Queue[dict[str, Any]],
        event: Any,
    ) -> None:
        """Publish the audit Event and the client-facing title projection."""

        public_event = event.public_dict()
        queue.put_nowait(
            {
                "type": "session_event_appended",
                "persisted": True,
                "event": public_event,
            }
        )
        payload = public_event.get("payload") or {}
        queue.put_nowait(
            {
                "type": "session_title",
                "persisted": True,
                "session_id": public_event.get("session_id"),
                "title": payload.get("title"),
                "source": payload.get("source"),
                "source_event_id": public_event.get("event_id"),
            }
        )

    @staticmethod
    async def _cancel_title_task(task: asyncio.Task[None] | None) -> None:
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

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
        run_id: str | None = None,
    ) -> dict[str, Any]:
        deps.agent_name = self.agent_name
        self._start_run_identity(session_id, deps, run_id=run_id)
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
            agent_id=deps.agent_id, agent_name=deps.agent_name, model_name=self._model_name,
            session_id=session_id,
            model_id=self._model_id,
            mode="non_stream",
        )
        self._set_tool_discovery_inputs(deps, message, context_refs)
        title_task: asyncio.Task[None] | None = None
        async with self._session_lock(session_id, agent_id=deps.agent_id, run_id=deps.run_id):
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
                await self._prepare_request_budget(session_id, deps)
            except BaseException as exc:
                self._log_run_terminal(run_event, status="cancelled" if isinstance(exc, (RunCancelled, asyncio.CancelledError)) else "failed",
                                       started_at=started_at, error=exc, phase="history")
                await self._finish_run_event(
                    run_event,
                    status="cancelled" if isinstance(exc, (RunCancelled, asyncio.CancelledError)) else "failed",
                    started_at=started_at,
                    usage=None,
                    error=type(exc).__name__, exception=exc,
                )
                raise
            title_task = await self._start_title_generation(
                session_id,
                deps,
                prepared,
                message=message,
                attachments=attachments,
                context_refs=context_refs,
            )
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
                self._log_run_terminal(run_event, status="cancelled" if isinstance(exc, (RunCancelled, asyncio.CancelledError)) else "failed",
                                       started_at=started_at, error=exc, phase="input")
                await self._finish_run_event(
                    run_event,
                    status=(
                        "cancelled"
                        if isinstance(exc, (RunCancelled, asyncio.CancelledError))
                        else "failed"
                    ),
                    started_at=started_at,
                    usage=None,
                    error=type(exc).__name__, exception=exc,
                )
                await self._cancel_title_task(title_task)
                raise
            self._prepare_context_state(deps, prepared, model_history=llm_history)
            progress = (
                MessagePersistenceCapability(self.store, history_length=len(llm_history),
                                      skip_user_prompt=prepared.pre_persisted_user,
                                      model_name=self._model_id, provider_id=self._provider_id)
                if callable(getattr(self.store, "append_message_parts", None)) else None
            )
            try:
                result = await self.agent.run(
                    user_prompt,
                    deps=deps,
                    message_history=llm_history,
                    usage_limits=self._run_limits(deps),
                    deferred_tool_results=deferred_tool_results,
                    output_type=_STREAM_OUTPUT_TYPE,
                    model_settings=self.model_settings,
                    conversation_id=deps.conversation_id,
                    run_id=deps.run_id,
                    cancellation_token=deps.cancellation_token,
                    capabilities=self._run_capabilities(deps, progress),
                )
            except BaseException as exc:
                self._log_run_terminal(run_event, status="cancelled" if isinstance(exc, (RunCancelled, asyncio.CancelledError)) else "failed",
                                       started_at=started_at, error=exc, phase="agent")
                if progress is not None:
                    # Non-streaming callers also preserve completed message facts.
                    with anyio.CancelScope(shield=True):
                        cancelled = isinstance(exc, (RunCancelled, asyncio.CancelledError))
                        recovered = (
                            mark_cancelled_response_interrupted(progress.messages)
                            if cancelled else _with_failed_response_diagnostic(progress.messages, _safe_run_failure(exc))
                        )
                        try:
                            await self._persist_run_messages(
                                session_id, recovered, deps,
                                pre_persisted_user=prepared.pre_persisted_user,
                                terminal_status="interrupted" if cancelled else "failed",
                            )
                        except Exception as persistence_exc:
                            logger.error("Non-stream terminal persistence failed: {}", type(persistence_exc).__name__)
                            await self._close_saved_messages(
                                session_id, deps, status="interrupted" if cancelled else "failed",
                            )
                await self._finish_run_event(
                    run_event,
                    status="cancelled" if isinstance(exc, (RunCancelled, asyncio.CancelledError)) else "failed",
                    started_at=started_at,
                    usage=getattr(exc, "usage", None),
                    error=type(exc).__name__, exception=exc,
                )
                await self._cancel_title_task(title_task)
                raise
            new_messages = (progress.canonical_messages(result.new_messages())
                            if progress is not None else result.new_messages())
            try:
                await self._attach_final_plan_snapshot(new_messages, deps)
                self._record_loaded_skills(deps, result.all_messages())
                await self._persist_run_messages(
                    session_id, new_messages, deps,
                    pre_persisted_user=prepared.pre_persisted_user,
                    repair_dangling_tool_calls=not isinstance(result.output, DeferredToolRequests),
                    terminal_status="pending" if isinstance(result.output, DeferredToolRequests) else "completed",
                )
            except BaseException as exc:
                self._log_run_terminal(run_event, status="cancelled" if isinstance(exc, (RunCancelled, asyncio.CancelledError)) else "failed",
                                       started_at=started_at, error=exc, phase="persistence")
                with anyio.CancelScope(shield=True):
                    cancelled = isinstance(exc, (RunCancelled, asyncio.CancelledError))
                    await self._close_saved_messages(
                        session_id, deps, status="interrupted" if cancelled else "failed",
                    )
                    await self._finish_run_event(
                        run_event, status="cancelled" if cancelled else "failed",
                        started_at=started_at, usage=result.usage, error=type(exc).__name__, exception=exc, phase="persistence",
                    )
                    await self._cancel_title_task(title_task)
                raise
            compaction_event_saved: bool | None = (True if deps.context_state is not None and deps.context_state.committed_event is not None else None)
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
        if title_task is not None:
            await title_task
        response = _build_run_result(
            result,
            self.ui_tool_hints,
            self.tool_display_names,
        )
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
            messages=result.new_messages(),
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
        log_event(
            "agent_run_pending" if terminal_status == "pending" else "agent_run_completed",
            conversation_id=deps.conversation_id,
            run_id=deps.run_id,
            agent_id=deps.agent_id, agent_name=deps.agent_name, model_name=self._model_name,
            session_id=session_id,
            **response["run_metrics"],
        )
        return response

    @asynccontextmanager
    async def _captured_stream(self, snapshot: _StreamRunSnapshot, **kwargs: Any):
        stream = None
        try:
            async with self.agent.run_stream_events(**kwargs) as stream:
                try:
                    yield stream
                finally:
                    # Preserve the official state before any resource teardown
                    # can raise. Never rebuild assistant messages from SSE text.
                    snapshot.capture(stream)
        finally:
            # The SDK commits partial responses while closing its background
            # run. Capture again after that close, including transport disconnect.
            if stream is not None:
                snapshot.capture(stream)

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
        snapshot: _StreamRunSnapshot,
        progress: StreamMessagePersistenceCapability | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Interleave Pydantic stream events with DeepAgent lifecycle progress."""

        async with self._captured_stream(
            snapshot,
            user_prompt=user_prompt,
            deps=deps,
            message_history=message_history,
            usage_limits=self._run_limits(deps),
            deferred_tool_results=deferred_tool_results,
            output_type=_STREAM_OUTPUT_TYPE,
            model_settings=self.model_settings,
            cancellation_token=cancellation_token,
            conversation_id=deps.conversation_id,
            run_id=deps.run_id,
            usage=usage,
            capabilities=self._run_capabilities(deps, progress),
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
                snapshot.capture(stream)
                pending = [task for task in (stream_task, status_task) if task is not None]
                for task in pending:
                    if not task.done():
                        task.cancel()
                # Child cleanup must not replace the original stream exception.
                await asyncio.gather(*pending, return_exceptions=True)

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
        target = budget.usable_request_tokens
        if target is None:
            return None
        run_context = RunContext(
            deps=deps,
            model=self.agent.model,
            usage=usage,
            usage_limits=self._run_limits(deps),
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
                budget_limit_tokens=target,
            )
            event = await self._persist_compaction(
                session_id,
                deps,
                raw_history=raw_history,
                projected_history=projected,
                trigger="provider_overflow",
                started_at=started_at,
                budget_limit_tokens=target,
            )
            if event is None:
                await state.finish_compaction("stale", "source_history_changed")
                logger.warning(
                    "Provider-overflow compaction became stale for session {}",
                    session_id,
                )
                return None
            # Continue from the exact anchor validated and stored, as automatic compaction does.
            state.committed_event = event
            projected = await self.store.get_history(session_id, agent_id=deps.agent_id)
            return projected, event

        except (RunCancelled, asyncio.CancelledError):
            await asyncio.shield(state.finish_compaction("cancelled", "cancelled"))
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
        deps.agent_name = self.agent_name
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
            agent_id=deps.agent_id, agent_name=deps.agent_name, model_name=self._model_name,
            session_id=session_id,
            model_id=self._model_id,
            mode="stream",
        )
        compaction_projection: list[ModelMessage] | None = None
        pre_saved_compaction_event: Any | None = None
        cancelled_run: RunCancelled | None = None
        cancelled_messages: list[ModelMessage] = []
        cancel_persistence_failed = False
        published_tool_calls: dict[str, str] = {}
        buffered_ui_tool_calls: dict[str, dict[str, Any]] = {}
        session_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        title_task: asyncio.Task[None] | None = None
        stream_snapshot = _StreamRunSnapshot()
        self._set_tool_discovery_inputs(deps, message, context_refs)
        async with self._session_lock(session_id, agent_id=deps.agent_id, run_id=deps.run_id):
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
                await self._prepare_request_budget(session_id, deps)
            except BaseException as exc:
                self._log_run_terminal(run_event, status="cancelled" if isinstance(exc, (RunCancelled, asyncio.CancelledError)) else "failed",
                                       started_at=started_at, error=exc, phase="history")
                failed_run_event = await self._finish_run_event(
                    run_event,
                    status=(
                        "cancelled"
                        if isinstance(exc, (RunCancelled, asyncio.CancelledError))
                        else "failed"
                    ),
                    started_at=started_at,
                    usage=None,
                    error=type(exc).__name__, exception=exc,
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
            title_task = await self._start_title_generation(
                session_id,
                deps,
                prepared,
                message=message,
                attachments=attachments,
                context_refs=context_refs,
                session_event_queue=session_events,
            )
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
                self._log_run_terminal(run_event, status="failed", started_at=started_at, error=exc, phase="input")
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
                    error=type(exc).__name__, exception=exc,
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
                await self._cancel_title_task(title_task)
                return
            self._prepare_context_state(
                deps,
                prepared,
                model_history=llm_history,
                event_queue=session_events,
            )
            result = None
            run_published = False
            progress = None
            owns_active_run = active_run is None or not active_run.request_owned
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
                progress = (
                    StreamMessagePersistenceCapability(
                        self.store, history_length=len(model_history),
                        skip_user_prompt=prepared.pre_persisted_user, model_name=self._model_id,
                        provider_id=self._provider_id,
                        event_queue=session_events,
                    )
                    if callable(getattr(self.store, "append_message_parts", None)) else None
                )
                for provider_attempt in range(2):
                    requests_before_attempt = model_usage.requests
                    if progress is not None:
                        progress.history_length = len(model_history)
                    try:
                        async with aclosing(self._stream_agent_events(
                            user_prompt=user_prompt,
                            message_history=model_history,
                            deferred_tool_results=deferred_tool_results,
                            deps=deps,
                            cancellation_token=active_run.cancellation_token,
                            usage=model_usage,
                            session_events=session_events,
                            snapshot=stream_snapshot,
                            progress=progress,
                        )) as stream_events:
                            async for source, event in stream_events:
                                if source == "status":
                                    status_event = event
                                    if status_event.get("type") == "context_compaction_progress":
                                        log_event(
                                            "context_compaction_phase",
                                            conversation_id=deps.conversation_id,
                                            run_id=deps.run_id,
                                            agent_id=deps.agent_id, agent_name=deps.agent_name, model_name=self._model_name,
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
                                    mapped_type = mapped.get("type")
                                    mapped_call_id = mapped.get("tool_call_id")
                                    if mapped_type in {"text", "thinking"}:
                                        mapped["item_id"] = f"live:{deps.run_id}:assistant"
                                    elif mapped_type in {
                                        "tool_call",
                                        "tool_result",
                                        "tool_call_removed",
                                    } and mapped_call_id:
                                        prefix = (
                                            "plan"
                                            if is_planning_tool(mapped.get("tool"))
                                            else "tool"
                                        )
                                        mapped["item_id"] = f"{prefix}:{mapped_call_id}"
                                    provider_retry_safe = False
                                    event_type = mapped.get("type")
                                    tool_call_id = mapped.get("tool_call_id")
                                    enriched = enrich_tool_event(mapped, self.ui_tool_hints)
                                    enriched = enrich_tool_event_display_name(
                                        enriched,
                                        self.tool_display_names,
                                    )
                                    if event_type == "tool_call" and tool_call_id:
                                        tool_name = str(mapped.get("tool") or "tool")
                                        if tool_name in self.ui_tool_hints:
                                            buffered_ui_tool_calls[tool_call_id] = enriched
                                            continue
                                    if event_type == "tool_call_removed" and tool_call_id:
                                        if buffered_ui_tool_calls.pop(tool_call_id, None) is not None:
                                            continue
                                    if event_type == "tool_result" and tool_call_id:
                                        buffered_call = buffered_ui_tool_calls.pop(tool_call_id, None)
                                        if buffered_call is not None:
                                            published_tool_calls[tool_call_id] = str(
                                                buffered_call.get("tool") or "tool"
                                            )
                                            yield buffered_call
                                    if event_type == "tool_call_removed":
                                        if not tool_call_id or tool_call_id not in published_tool_calls:
                                            continue
                                        published_tool_calls.pop(tool_call_id, None)
                                    elif event_type == "tool_call" and tool_call_id:
                                        published_tool_calls[tool_call_id] = str(mapped.get("tool") or "tool")
                                    elif event_type == "tool_result" and tool_call_id:
                                        published_tool_calls.pop(tool_call_id, None)
                                    yield enriched
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
                    new_messages = (progress.canonical_messages(result.new_messages())
                                    if progress is not None else result.new_messages())
                    await self._attach_final_plan_snapshot(new_messages, deps)
                    if progress is not None:
                        # Insert only remaining real facts and close statuses before
                        # releasing the session lock / admitting a new Run.
                        await self._persist_run_messages(
                            session_id, new_messages, deps,
                            pre_persisted_user=prepared.pre_persisted_user,
                            repair_dangling_tool_calls=not deferred_pending,
                            terminal_status="pending" if deferred_pending else "completed",
                            result_queue=session_events,
                        )
                    else:
                        self._publish_run_complete(
                            persister, session_id, deps, new_messages,
                            pre_persisted_user=prepared.pre_persisted_user,
                            repair_dangling_tool_calls=not deferred_pending,
                            terminal_status="pending" if deferred_pending else "completed",
                            result_queue=session_events,
                        )
                    run_published = True
                    if not deferred_pending and pre_saved_compaction_event is None:
                        compaction_projection = result.all_messages()

            except RunCancelled as exc:
                cancelled_run = exc
            except asyncio.CancelledError as exc:
                active_run.cancellation_token.cancel()
                cancelled_run = RunCancelled.from_cancellation(exc) or RunCancelled(
                    "Transport disconnected",
                    messages=stream_snapshot.new_messages,
                    usage=stream_snapshot.usage,
                )
                await self._cancel_title_task(title_task)
                raise
            except GeneratorExit:
                active_run.cancellation_token.cancel()
                cancelled_run = RunCancelled(
                    "Transport disconnected",
                    messages=stream_snapshot.new_messages,
                    usage=stream_snapshot.usage,
                )
                await self._cancel_title_task(title_task)
                raise
            except Exception as exc:  # noqa: BLE001 - surface as an SSE error event
                logger.exception("Agent stream failed for session {}", session_id)
                failure_diagnostic = _safe_run_failure(exc)
                official_failed = stream_snapshot.new_messages
                if progress is not None:
                    official_failed = (progress.canonical_messages(official_failed)
                                       if official_failed else progress.messages)
                failed_messages = _with_failed_response_diagnostic(
                    official_failed, failure_diagnostic,
                )
                history_persisted = prepared.pre_persisted_user
                if failed_messages:
                    try:
                        await self._attach_final_plan_snapshot(failed_messages, deps)
                        if stream_snapshot.all_messages:
                            self._record_loaded_skills(deps, stream_snapshot.all_messages)
                        history_persisted = await self._persist_run_messages(
                            session_id,
                            failed_messages,
                            deps,
                            pre_persisted_user=prepared.pre_persisted_user,
                            repair_dangling_tool_calls=True,
                            dangling_tool_message=failure_diagnostic["message"],
                            terminal_status="failed",
                        ) or history_persisted
                    except Exception as persistence_exc:
                        history_persisted = False
                        logger.exception(
                            "Failed-run history persistence failed for session {}: {}",
                            session_id,
                            persistence_exc,
                        )
                        await self._close_saved_messages(session_id, deps, status="failed")
                else:
                    history_persisted = await self._close_saved_messages(
                        session_id, deps, status="failed",
                    ) and history_persisted
                failed_run_event = await self._finish_run_event(
                    run_event, status="failed", started_at=started_at,
                    usage=stream_snapshot.usage or model_usage,
                    error=type(exc).__name__, exception=exc,
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
                    agent_id=deps.agent_id, agent_name=deps.agent_name, model_name=self._model_name,
                    session_id=session_id,
                    model_id=self._model_id,
                    status="failed",
                    duration_ms=max(0, round((time.perf_counter() - started_at) * 1_000)),
                    error_type=type(exc).__name__,
                )
                # Ordinary tools may have already been shown before execution
                # failed. Close every unresolved live card as well as history;
                # rejected UI calls remain buffered and are never published.
                for call_id, tool_name in published_tool_calls.items():
                    failed_tool = events.tool_result(tool_name, failure_diagnostic["message"], call_id)
                    failed_tool["outcome"] = "failed"
                    prefix = "plan" if is_planning_tool(tool_name) else "tool"
                    failed_tool["item_id"] = f"{prefix}:{call_id}"
                    yield enrich_tool_event_display_name(
                        enrich_tool_event(failed_tool, self.ui_tool_hints), self.tool_display_names,
                    )
                published_tool_calls.clear()
                yield events.error(
                    format_stream_error(exc),
                    history_persisted=history_persisted,
                )
                await self._cancel_title_task(title_task)
                return
            finally:
                cancelled_run_event = None
                if cancelled_run is not None:
                    self._log_run_terminal(
                        run_event, status="cancelled", started_at=started_at, usage=cancelled_run.usage,
                        reason=active_run.cancel_reason or "task_cancelled_unknown",
                    )
                if cancelled_run is not None and not run_published:
                    official_cancelled = cancelled_run.new_messages()
                    if progress is not None:
                        official_cancelled = (progress.canonical_messages(official_cancelled)
                                              if official_cancelled else progress.messages)
                    cancelled_messages = mark_cancelled_response_interrupted(official_cancelled)
                    # Transport cancellation re-raises before the normal cancelled
                    # SSE path below. Close its durable lifecycle here as well,
                    # even when the Provider has not emitted a first response.
                    # Shield from the SSE transport's level-triggered cancel scope;
                    # terminal writes still have their own bounded timeout.
                    with anyio.CancelScope(shield=True):
                        try:
                            await self._attach_final_plan_snapshot(cancelled_messages, deps)
                        except Exception as exc:
                            logger.warning("Cancelled plan snapshot unavailable: {}", type(exc).__name__)
                        if callable(getattr(self.store, "append_message_parts", None)):
                            try:
                                if cancelled_messages:
                                    await self._persist_run_messages(
                                        session_id, cancelled_messages, deps,
                                        pre_persisted_user=prepared.pre_persisted_user,
                                        repair_dangling_tool_calls=False,
                                        terminal_status="interrupted", result_queue=session_events,
                                    )
                                else:
                                    await self._close_saved_messages(session_id, deps, status="interrupted")
                            except Exception:
                                cancel_persistence_failed = True
                                logger.exception("Cancelled message finalization failed")
                                await self._close_saved_messages(session_id, deps, status="interrupted")
                        else:
                            self._publish_run_cancelled(
                                persister, session_id, deps,
                                pre_persisted_user=prepared.pre_persisted_user,
                                official_messages=cancelled_messages, result_queue=session_events,
                            )
                        durable_messages = incremental_append_messages(
                            cancelled_messages,
                            skip_user_prompt=prepared.pre_persisted_user,
                        )
                        if not any(isinstance(item, ModelResponse) for item in durable_messages):
                            cancelled_run_event = await self._finish_run_event(
                                run_event, status="cancelled", started_at=started_at,
                                usage=cancelled_run.usage,
                            )
                if owns_active_run:
                    self._active_runs.unregister(active_run)

        if cancelled_run is not None:
            await self._cancel_title_task(title_task)
            pending = cancelled_messages
            history_persisted = False
            try:
                # The runner's session lock is released before the persister acquires it.
                await persister.flush(session_id, agent_id=deps.agent_id)
                history_persisted = (bool(pending) or prepared.pre_persisted_user) and not cancel_persistence_failed
                while not session_events.empty():
                    yield session_events.get_nowait()
            except Exception as exc:  # noqa: BLE001 - surface persistence state in SSE
                logger.exception(
                    "Cancelled history persistence failed for session {}: {}",
                    session_id,
                    exc,
                )
            for tool_call_id, tool_name in list(published_tool_calls.items()):
                removed = events.tool_call_removed(
                    tool_name,
                    tool_call_id,
                    reason="cancelled",
                )
                prefix = "plan" if is_planning_tool(tool_name) else "tool"
                removed["item_id"] = f"{prefix}:{tool_call_id}"
                yield enrich_tool_event_display_name(
                    removed,
                    self.tool_display_names,
                )
            if cancelled_run_event is None:
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
                    messages=pending,
                ),
            )
            return

        if result is not None:
            self._record_loaded_skills(deps, result.all_messages())
            try:
                await persister.flush(session_id, agent_id=deps.agent_id)
            except Exception as exc:
                self._log_run_terminal(run_event, status="failed", started_at=started_at, error=exc, phase="persistence")
                failed_run_event = await self._finish_run_event(
                    run_event,
                    status="failed",
                    started_at=started_at,
                    usage=result.usage,
                    error="EventPersistenceFailed", phase="persistence",
                )
                if failed_run_event is not None:
                    yield {
                        "type": "session_event_updated",
                        "persisted": True,
                        "event": failed_run_event.public_dict(),
                    }
                yield events.error(f"会话事件持久化失败：{type(exc).__name__}")
                await self._cancel_title_task(title_task)
                return
            while not session_events.empty():
                yield session_events.get_nowait()
            for buffered_call in buffered_ui_tool_calls.values():
                tool_call_id = str(buffered_call.get("tool_call_id") or "")
                tool_name = str(buffered_call.get("tool") or "tool")
                if tool_call_id:
                    published_tool_calls[tool_call_id] = tool_name
                yield buffered_call
            buffered_ui_tool_calls.clear()
        if title_task is not None:
            await title_task
            while not session_events.empty():
                yield session_events.get_nowait()
        committed_compaction = deps.context_state.committed_event if deps.context_state is not None else None
        compaction_event_saved: bool | None = (
            True if pre_saved_compaction_event is not None or committed_compaction is not None else None
        )
        if compaction_projection is not None and committed_compaction is None:
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
            messages=result.new_messages() if result is not None else [],
        )
        log_event(
            "agent_run_pending" if metrics["status"] == "pending" else "agent_run_completed",
            conversation_id=deps.conversation_id,
            run_id=deps.run_id,
            agent_id=deps.agent_id, agent_name=deps.agent_name, model_name=self._model_name,
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
            self.tool_display_names,
            context=final_context,
            conversation_id=deps.conversation_id,
            run_id=deps.run_id,
            run_metrics=metrics,
        )


def _build_run_result(
    result: Any,
    ui_tool_hints: dict[str, dict[str, Any]],
    tool_display_names: dict[str, str],
) -> dict[str, Any]:
    output = result.output
    if isinstance(output, DeferredToolRequests):
        deferred = serialize_deferred(output)
        return {
            "output": None,
            "status": "pending",
            "deferred": enrich_deferred_display_names(
                enrich_deferred(deferred, ui_tool_hints),
                tool_display_names,
            ),
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
    tool_display_names: dict[str, str],
    *,
    context: dict[str, Any] | None = None,
    conversation_id: str | None = None,
    run_id: str | None = None,
    run_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output = result.output
    usage = _usage_dict(result)
    if isinstance(output, DeferredToolRequests):
        deferred = enrich_deferred_display_names(
            enrich_deferred(serialize_deferred(output), ui_tool_hints),
            tool_display_names,
        )
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
    budget_limit_tokens: int | None = None,
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

        "reduction_ratio": telemetry.get("reduction_ratio", 0.0),
        "summary_calls": telemetry.get("summary_calls", 0),
        "summary_chunks": telemetry.get("summary_chunks", 0),
        "candidate_tokens": telemetry.get("candidate_tokens"),
        "retained_tail_tokens": telemetry.get("retained_tail_tokens"),
        "evidence_tokens": telemetry.get("evidence_tokens"),
        "summary_tokens": telemetry.get("summary_tokens"),
        "usage": _run_usage_dict(usage),
        "compaction_event": compaction_event,
        "conversation_id": conversation_id,
        "run_id": run_id,
        "run_metrics": run_metrics,
    }
