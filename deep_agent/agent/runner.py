from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from pydantic_ai import Agent, AgentRunResultEvent
from pydantic_ai.usage import UsageLimits

from deep_agent.agent import events
from deep_agent.agent.deps import AgentDeps
from loguru import logger

from deep_agent.memory.base import SessionStore
from deep_agent.memory.history import filter_ephemeral_agent_messages


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
            result = await self.agent.run(
                prompt,
                deps=deps,
                message_history=history,
                usage_limits=self.usage_limits,
            )
            await self.store.append(
                session_id,
                filter_ephemeral_agent_messages(result.new_messages()),
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
            result = None
            try:
                async with self.agent.run_stream_events(
                    prompt,
                    deps=deps,
                    message_history=history,
                    usage_limits=self.usage_limits,
                ) as stream:
                    async for event in stream:
                        if isinstance(event, AgentRunResultEvent):
                            result = event.result
                            continue
                        mapped = events.map_event(event)
                        if mapped is not None:
                            yield mapped
                await self.store.append(
                    session_id,
                    filter_ephemeral_agent_messages(result.new_messages()),
                    agent_id=deps.agent_id,
                    user_id=deps.user,
                )
            except Exception as exc:  # noqa: BLE001 - surface as an SSE error event
                logger.exception("Agent stream failed for session {}", session_id)
                yield events.error(str(exc))
                return

        yield events.final(_serialize_output(result.output), _usage_dict(result))



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
