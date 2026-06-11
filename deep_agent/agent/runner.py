from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from pydantic_ai import Agent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolCallPart,
)
from pydantic_ai.usage import UsageLimits

from deep_agent.agent import events
from deep_agent.agent.deps import AgentDeps
from deep_agent.core.logging import get_logger
from deep_agent.memory.base import SessionStore

logger = get_logger(__name__)


class AgentRunner:
    """Transport-agnostic wrapper around a Pydantic AI agent.

    ``run`` / ``run_stream`` take a session id + prompt, load history from the
    store, run the agent loop (managed by Pydantic AI), persist new messages,
    and (for streaming) map Pydantic AI events to plain serializable dicts.

    This class has no FastAPI dependency, so it can later be wrapped as a node
    in a pydantic-graph orchestration without changes.
    """

    def __init__(self, agent: Agent[AgentDeps, Any], store: SessionStore, usage_limits: UsageLimits) -> None:
        self.agent = agent
        self.store = store
        self.usage_limits = usage_limits

    @asynccontextmanager
    async def _session_lock(self, session_id: str) -> AsyncIterator[None]:
        lock = getattr(self.store, "lock", None)
        if callable(lock):
            async with lock(session_id):
                yield
        else:
            yield

    async def run(self, session_id: str, prompt: str, deps: AgentDeps) -> dict[str, Any]:
        async with self._session_lock(session_id):
            history = await self.store.get_history(session_id)
            async with self.agent:
                result = await self.agent.run(
                    prompt,
                    deps=deps,
                    message_history=history,
                    usage_limits=self.usage_limits,
                )
            await self.store.append(
                session_id,
                result.new_messages(),
                agent_id=deps.agent_id,
                user_id=deps.user,
            )
        return {
            "output": _serialize_output(result.output),
            "usage": _usage_dict(result),
        }

    async def run_stream(self, session_id: str, prompt: str, deps: AgentDeps) -> AsyncIterator[dict[str, Any]]:
        async with self._session_lock(session_id):
            history = await self.store.get_history(session_id)
            try:
                async with self.agent:
                    async with self.agent.iter(
                        prompt,
                        deps=deps,
                        message_history=history,
                        usage_limits=self.usage_limits,
                    ) as run:
                        async for node in run:
                            if Agent.is_model_request_node(node):
                                async with node.stream(run.ctx) as stream:
                                    async for event in stream:
                                        mapped = _map_model_event(event)
                                        if mapped is not None:
                                            yield mapped
                            elif Agent.is_call_tools_node(node):
                                async with node.stream(run.ctx) as stream:
                                    async for event in stream:
                                        mapped = _map_tool_event(event)
                                        if mapped is not None:
                                            yield mapped
                        result = run.result
                    await self.store.append(
                        session_id,
                        result.new_messages(),
                        agent_id=deps.agent_id,
                        user_id=deps.user,
                    )
            except Exception as exc:  # noqa: BLE001 - surface as an SSE error event
                logger.exception("Agent stream failed for session %s", session_id)
                yield events.error(str(exc))
                return

        yield events.final(_serialize_output(result.output), _usage_dict(result))


def _safe_args(part: ToolCallPart) -> Any:
    try:
        return part.args_as_dict()
    except Exception:  # noqa: BLE001
        return getattr(part, "args", None)


def _map_model_event(event: Any) -> dict[str, Any] | None:
    if isinstance(event, PartStartEvent):
        part = event.part
        if isinstance(part, TextPart) and part.content:
            return events.text_delta(part.content)
        if isinstance(part, ToolCallPart):
            return events.tool_call(part.tool_name, _safe_args(part), getattr(part, "tool_call_id", None))
        return None
    if isinstance(event, PartDeltaEvent):
        delta = event.delta
        if isinstance(delta, TextPartDelta) and delta.content_delta:
            return events.text_delta(delta.content_delta)
        return None
    return None


def _map_tool_event(event: Any) -> dict[str, Any] | None:
    if isinstance(event, FunctionToolCallEvent):
        part = event.part
        return events.tool_call(part.tool_name, _safe_args(part), getattr(part, "tool_call_id", None))
    if isinstance(event, FunctionToolResultEvent):
        result_part = event.result
        return events.tool_result(
            getattr(result_part, "tool_name", None),
            _to_jsonable(getattr(result_part, "content", None)),
            getattr(result_part, "tool_call_id", None),
        )
    return None


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, (list, dict)):
        return value
    return str(value)


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
