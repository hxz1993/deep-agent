from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from pydantic_ai import Agent, AgentRunResultEvent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.usage import UsageLimits

from deep_agent.agent import events
from deep_agent.agent.deferred import serialize_deferred
from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities.ui.registry import enrich_deferred, enrich_tool_event
from loguru import logger

from deep_agent.db.conversation import SessionStore
from deep_agent.agent.history import incremental_append_messages

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
    ) -> None:
        self.agent = agent
        self.store = store
        self.usage_limits = usage_limits
        self.ui_tool_hints = ui_tool_hints or {}

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
        prompt: str,
        deps: AgentDeps,
        *,
        branch_from: int | None = None,
    ) -> tuple[list[ModelMessage], bool, int | None]:
        if branch_from is not None:
            await self.store.truncate_history(
                session_id,
                agent_id=deps.agent_id,
                keep_count=branch_from,
            )
        history = await self.store.get_history(session_id, agent_id=deps.agent_id)
        pre_persisted_user = False
        user_branch_point: int | None = None
        if prompt.strip():
            user_request = ModelRequest(parts=[UserPromptPart(content=prompt)])
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

    async def _history_meta(
        self,
        session_id: str,
        deps: AgentDeps,
        *,
        user_branch_point: int | None,
    ) -> tuple[int, int | None]:
        count = len(await self.store.get_history(session_id, agent_id=deps.agent_id))
        return count, user_branch_point

    async def _persist_run_messages(
        self,
        session_id: str,
        new_messages: list[ModelMessage],
        deps: AgentDeps,
        *,
        pre_persisted_user: bool,
    ) -> None:
        to_append = incremental_append_messages(new_messages, skip_user_prompt=pre_persisted_user)
        if not to_append:
            return
        await self.store.append(
            session_id,
            to_append,
            agent_id=deps.agent_id,
            user_id=deps.user,
        )

    async def run(
        self,
        session_id: str,
        prompt: str,
        deps: AgentDeps,
        *,
        deferred_tool_results: DeferredToolResults | None = None,
        branch_from: int | None = None,
    ) -> dict[str, Any]:
        async with self._session_lock(session_id, agent_id=deps.agent_id):
            history, pre_persisted_user, _ = await self._prepare_history(
                session_id,
                prompt,
                deps,
                branch_from=branch_from,
            )
            result = await self.agent.run(
                prompt,
                deps=deps,
                message_history=history,
                usage_limits=self.usage_limits,
                deferred_tool_results=deferred_tool_results,
                output_type=_STREAM_OUTPUT_TYPE,
            )
            await self._persist_run_messages(
                session_id,
                result.new_messages(),
                deps,
                pre_persisted_user=pre_persisted_user,
            )
        return _build_run_result(result, self.ui_tool_hints)

    async def run_stream(
        self,
        session_id: str,
        prompt: str,
        deps: AgentDeps,
        *,
        deferred_tool_results: DeferredToolResults | None = None,
        branch_from: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        async with self._session_lock(session_id, agent_id=deps.agent_id):
            history, pre_persisted_user, user_branch_point = await self._prepare_history(
                session_id,
                prompt,
                deps,
                branch_from=branch_from,
            )
            result = None
            try:
                async with self.agent.run_stream_events(
                    prompt,
                    deps=deps,
                    message_history=history,
                    usage_limits=self.usage_limits,
                    deferred_tool_results=deferred_tool_results,
                    output_type=_STREAM_OUTPUT_TYPE,
                ) as stream:
                    async for event in stream:
                        if isinstance(event, AgentRunResultEvent):
                            result = event.result
                            continue
                        mapped = events.map_event(event)
                        if mapped is not None:
                            yield enrich_tool_event(mapped, self.ui_tool_hints)
                await self._persist_run_messages(
                    session_id,
                    result.new_messages(),
                    deps,
                    pre_persisted_user=pre_persisted_user,
                )
            except Exception as exc:  # noqa: BLE001 - surface as an SSE error event
                logger.exception("Agent stream failed for session {}", session_id)
                msg_count, branch_pt = await self._history_meta(
                    session_id,
                    deps,
                    user_branch_point=user_branch_point if pre_persisted_user else None,
                )
                yield events.error(
                    str(exc),
                    history_persisted=pre_persisted_user,
                    model_message_count=msg_count if pre_persisted_user else None,
                    user_branch_point=branch_pt,
                )
                return

        msg_count, branch_pt = await self._history_meta(
            session_id,
            deps,
            user_branch_point=user_branch_point,
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
