from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from pydantic_ai import Agent, AgentRunResultEvent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ModelMessage, ModelRequest, UserContent, UserPromptPart
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from deep_agent.agent import events
from deep_agent.agent.attachments import (
    has_user_turn,
    model_supports_image_input,
    refs_from_attachments,
    resolve_history_for_llm,
    resolve_user_prompt,
)
from deep_agent.agent.deferred import serialize_deferred
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister, PersistEvent
from deep_agent.agent.limits import format_stream_error
from deep_agent.agent.history import (
    _extract_assistant_text,
    first_user_prompt,
    incremental_append_messages,
    repair_unprocessed_tool_calls,
)
from deep_agent.agent.stream_accumulator import StreamAccumulator
from deep_agent.capabilities.ui.registry import enrich_deferred, enrich_tool_event
from loguru import logger

from deep_agent.db.conversation import SessionStore
from deep_agent.schemas.attachment import AttachmentRef

_STREAM_OUTPUT_TYPE: list[Any] = [str, DeferredToolRequests]


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
        input_modalities: list[str] | None = None,
        capabilities_source: str | None = None,
        model_base_url: str | None = None,
    ) -> None:
        self.agent = agent
        self.store = store
        self.usage_limits = usage_limits
        self.ui_tool_hints = ui_tool_hints or {}
        self.model_settings = model_settings
        self._input_modalities = input_modalities
        self._capabilities_source = capabilities_source
        self._model_base_url = model_base_url

    @asynccontextmanager
    async def _session_lock(self, session_id: str, *, agent_id: str | None) -> AsyncIterator[None]:
        lock = getattr(self.store, "lock", None)
        if callable(lock):
            async with lock(session_id, agent_id=agent_id):
                yield
        else:
            yield

    async def _prepare_history(
        self,
        session_id: str,
        message: str,
        deps: AgentDeps,
        *,
        attachments: list[AttachmentRef] | None = None,
        branch_from: int | None = None,
        deferred_tool_results: DeferredToolResults | None = None,
        persister: EventPersister | None = None,
    ) -> tuple[list[ModelMessage], bool, int | None]:
        if branch_from is not None:
            await self.store.truncate_history(
                session_id,
                agent_id=deps.agent_id,
                keep_count=branch_from,
            )
        history = await self.store.get_history(session_id, agent_id=deps.agent_id)
        if not deferred_tool_results:
            repaired = repair_unprocessed_tool_calls(history)
            if repaired is not history:
                replace_history = getattr(self.store, "replace_history", None)
                if callable(replace_history):
                    await replace_history(
                        session_id,
                        repaired,
                        agent_id=deps.agent_id,
                    )
                history = repaired
        pre_persisted_user = False
        user_branch_point: int | None = None
        if has_user_turn(message, attachments):
            content = refs_from_attachments(message, attachments)
            user_request = ModelRequest(parts=[UserPromptPart(content=content)])
            if persister is not None:
                persister.publish(
                    PersistEvent(
                        session_id=session_id,
                        agent_id=deps.agent_id,
                        user_id=deps.user,
                        kind="user_turn",
                        user_message=user_request,
                    )
                )
            else:
                await self.store.append(
                    session_id,
                    [user_request],
                    agent_id=deps.agent_id,
                    user_id=deps.user,
                )
            history = [*history, user_request]
            pre_persisted_user = True
            user_branch_point = len(history) - 1
        return history, pre_persisted_user, user_branch_point

    def _vision_capable(self) -> bool:
        model = self.agent.model
        model_name = getattr(model, "model_name", None) or str(model)
        return model_supports_image_input(
            model_name,
            db_modalities=self._input_modalities,
            capabilities_source=self._capabilities_source,
            base_url=self._model_base_url,
        )

    async def _llm_inputs(
        self,
        history: list[ModelMessage],
        message: str,
        attachments: list[AttachmentRef] | None,
        deps: AgentDeps,
        *,
        pre_persisted_user: bool,
    ) -> tuple[list[ModelMessage], str | list[UserContent] | None]:
        vision_capable = self._vision_capable()
        current_user_index = len(history) - 1 if pre_persisted_user else None
        llm_history = await resolve_history_for_llm(
            history,
            current_user_index=current_user_index,
            request_headers=deps.request_headers,
            vision_capable=vision_capable,
        )
        if pre_persisted_user:
            return llm_history, None
        if has_user_turn(message, attachments):
            return llm_history, await resolve_user_prompt(
                message,
                attachments,
                deps.request_headers,
                vision_capable=vision_capable,
            )
        return llm_history, message or None

    def _stream_history_meta(
        self,
        history: list[ModelMessage],
        *,
        user_branch_point: int | None,
        pending_messages: list[ModelMessage] | None = None,
        pre_persisted_user: bool = False,
    ) -> tuple[int, int | None]:
        count = len(history)
        if pending_messages:
            count += len(
                incremental_append_messages(
                    pending_messages,
                    skip_user_prompt=pre_persisted_user,
                )
            )
        return count, user_branch_point

    async def _persist_run_messages(
        self,
        session_id: str,
        new_messages: list[ModelMessage],
        deps: AgentDeps,
        *,
        pre_persisted_user: bool,
        repair_dangling_tool_calls: bool = True,
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
    ) -> None:
        persister.publish(
            PersistEvent(
                session_id=session_id,
                agent_id=deps.agent_id,
                user_id=deps.user,
                kind="run_complete",
                new_messages=new_messages,
                skip_user_prompt=pre_persisted_user,
                repair_dangling_tool_calls=repair_dangling_tool_calls,
            )
        )

    def _publish_run_cancelled(
        self,
        persister: EventPersister,
        session_id: str,
        deps: AgentDeps,
        *,
        pre_persisted_user: bool,
        result: Any | None,
        accumulator: StreamAccumulator,
        deferred_tool_results: DeferredToolResults | None,
    ) -> None:
        if result is not None:
            persister.publish(
                PersistEvent(
                    session_id=session_id,
                    agent_id=deps.agent_id,
                    user_id=deps.user,
                    kind="run_cancelled",
                    result_messages=result.new_messages(),
                    skip_user_prompt=pre_persisted_user,
                )
            )
        elif accumulator.has_content():
            persister.publish(
                PersistEvent(
                    session_id=session_id,
                    agent_id=deps.agent_id,
                    user_id=deps.user,
                    kind="run_cancelled",
                    accumulator=accumulator,
                    skip_user_prompt=pre_persisted_user,
                    include_tool_calls=deferred_tool_results is None,
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
            # 步骤 1：判断是否首次交换
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
            )

            if not title:
                title = user_text

            if title:
                latest = await self.store.get_session_title(session_id, agent_id=deps.agent_id)
                if latest and (not user_text or latest != user_text):
                    return
                await self.store.update_session_title(session_id, deps.agent_id, title)

        except Exception as e:
            logger.warning("Title generation failed for session {}: {}", session_id, e)

    async def run(
        self,
        session_id: str,
        message: str,
        deps: AgentDeps,
        *,
        attachments: list[AttachmentRef] | None = None,
        deferred_tool_results: DeferredToolResults | None = None,
        branch_from: int | None = None,
    ) -> dict[str, Any]:
        async with self._session_lock(session_id, agent_id=deps.agent_id):
            history, pre_persisted_user, _ = await self._prepare_history(
                session_id,
                message,
                deps,
                attachments=attachments,
                branch_from=branch_from,
                deferred_tool_results=deferred_tool_results,
            )
            llm_history, user_prompt = await self._llm_inputs(
                history,
                message,
                attachments,
                deps,
                pre_persisted_user=pre_persisted_user,
            )
            result = await self.agent.run(
                user_prompt,
                deps=deps,
                message_history=llm_history,
                usage_limits=self.usage_limits,
                deferred_tool_results=deferred_tool_results,
                output_type=_STREAM_OUTPUT_TYPE,
                model_settings=self.model_settings,
            )
            await self._persist_run_messages(
                session_id,
                result.new_messages(),
                deps,
                pre_persisted_user=pre_persisted_user,
                repair_dangling_tool_calls=not isinstance(result.output, DeferredToolRequests),
            )
        return _build_run_result(result, self.ui_tool_hints)

    async def run_stream(
        self,
        session_id: str,
        message: str,
        deps: AgentDeps,
        *,
        attachments: list[AttachmentRef] | None = None,
        deferred_tool_results: DeferredToolResults | None = None,
        branch_from: int | None = None,
        persister: EventPersister,
    ) -> AsyncIterator[dict[str, Any]]:
        async with self._session_lock(session_id, agent_id=deps.agent_id):
            history, pre_persisted_user, user_branch_point = await self._prepare_history(
                session_id,
                message,
                deps,
                attachments=attachments,
                branch_from=branch_from,
                deferred_tool_results=deferred_tool_results,
                persister=persister,
            )
            llm_history, user_prompt = await self._llm_inputs(
                history,
                message,
                attachments,
                deps,
                pre_persisted_user=pre_persisted_user,
            )
            result = None
            cancelled = False
            run_published = False
            accumulator = StreamAccumulator()

            try:
                async with self.agent.run_stream_events(
                    user_prompt,
                    deps=deps,
                    message_history=llm_history,
                    usage_limits=self.usage_limits,
                    deferred_tool_results=deferred_tool_results,
                    output_type=_STREAM_OUTPUT_TYPE,
                    model_settings=self.model_settings,
                ) as stream:
                    while True:
                        try:
                            event = await stream.__anext__()
                        except StopAsyncIteration:
                            break
                        if isinstance(event, AgentRunResultEvent):
                            result = event.result
                            continue
                        mapped = events.map_event(event)
                        if mapped is not None:
                            accumulator.ingest(mapped)
                            try:
                                yield enrich_tool_event(mapped, self.ui_tool_hints)
                            except asyncio.CancelledError:
                                cancelled = True
                                break
                if not cancelled and result is not None:
                    deferred_pending = isinstance(result.output, DeferredToolRequests)
                    self._publish_run_complete(
                        persister,
                        session_id,
                        deps,
                        result.new_messages(),
                        pre_persisted_user=pre_persisted_user,
                        repair_dangling_tool_calls=not deferred_pending,
                    )
                    run_published = True

                    if branch_from is None:
                        asyncio.create_task(self._maybe_generate_title(
                            session_id, deps, result.new_messages(),
                        ))
            except asyncio.CancelledError:
                cancelled = True
            except GeneratorExit:
                cancelled = True
                raise
            except Exception as exc:  # noqa: BLE001 - surface as an SSE error event
                logger.exception("Agent stream failed for session {}", session_id)
                msg_count, branch_pt = self._stream_history_meta(
                    history,
                    user_branch_point=user_branch_point if pre_persisted_user else None,
                )
                yield events.error(
                    format_stream_error(exc),
                    history_persisted=pre_persisted_user,
                    model_message_count=msg_count if pre_persisted_user else None,
                    user_branch_point=branch_pt,
                )
                return
            finally:
                if cancelled and not run_published:
                    self._publish_run_cancelled(
                        persister,
                        session_id,
                        deps,
                        pre_persisted_user=pre_persisted_user,
                        result=result,
                        accumulator=accumulator,
                        deferred_tool_results=deferred_tool_results,
                    )

            if cancelled:
                pending = (
                    result.new_messages()
                    if result is not None
                    else (
                        accumulator.to_model_messages(
                            include_tool_calls=deferred_tool_results is None,
                        )
                        if accumulator.has_content()
                        else None
                    )
                )
                msg_count, branch_pt = self._stream_history_meta(
                    history,
                    user_branch_point=user_branch_point,
                    pending_messages=pending,
                    pre_persisted_user=pre_persisted_user,
                )
                yield events.cancelled(
                    history_persisted=accumulator.has_content() or pre_persisted_user,
                    model_message_count=msg_count,
                    user_branch_point=branch_pt,
                )
                return

        msg_count, branch_pt = self._stream_history_meta(
            history,
            user_branch_point=user_branch_point,
            pending_messages=result.new_messages() if result is not None else None,
            pre_persisted_user=pre_persisted_user,
        )
        yield _final_event(
            result,
            self.ui_tool_hints,
            model_message_count=msg_count,
            user_branch_point=branch_pt,
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
    model_message_count: int | None = None,
    user_branch_point: int | None = None,
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
            model_message_count=model_message_count,
            user_branch_point=user_branch_point,
        )
    return events.final(
        _serialize_output(output),
        usage,
        status="completed",
        model_message_count=model_message_count,
        user_branch_point=user_branch_point,
    )


def _serialize_output(output: Any) -> Any:
    if isinstance(output, (str, int, float, bool)) or output is None:
        return output
    if hasattr(output, "model_dump"):
        return output.model_dump()
    return str(output)


def _usage_dict(result: Any) -> dict[str, Any]:
    usage = result.usage
    if usage is None:
        return {}
    return {
        "requests": getattr(usage, "requests", None),
        "input_tokens": getattr(usage, "input_tokens", getattr(usage, "request_tokens", None)),
        "output_tokens": getattr(usage, "output_tokens", getattr(usage, "response_tokens", None)),
        "total_tokens": getattr(usage, "total_tokens", None),
    }
