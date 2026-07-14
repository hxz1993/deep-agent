from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any, Literal

from loguru import logger
from pydantic_ai.messages import ModelMessage, ModelRequest

from deep_agent.agent.history import incremental_append_messages, repair_unprocessed_tool_calls
from deep_agent.agent.stream_accumulator import StreamAccumulator
from deep_agent.db.conversation import SessionStore
from deep_agent.db.session_scope import runtime_session_key

_SENTINEL = object()


@dataclass
class _FlushMarker:
    done: asyncio.Event


@dataclass(frozen=True)
class PersistEvent:
    session_id: str
    agent_id: str | None
    user_id: str | None
    kind: Literal["user_turn", "run_complete", "run_cancelled"]
    user_message: ModelRequest | None = None
    new_messages: list[ModelMessage] | None = None
    skip_user_prompt: bool = False
    accumulator: StreamAccumulator | None = None
    result_messages: list[ModelMessage] | None = None
    include_tool_calls: bool = True
    repair_dangling_tool_calls: bool = True


class EventPersister:
    """Background consumer: decouples SSE-cancelable runs from SessionStore writes."""

    def __init__(self, store: SessionStore) -> None:
        self._store = store
        self._queues: dict[str, asyncio.Queue[Any]] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._session_ids: dict[str, tuple[str, str | None]] = {}
        self._closed = False

    async def start(self) -> None:
        return

    def publish(self, ev: PersistEvent) -> None:
        if self._closed:
            logger.warning("EventPersister closed, dropping {} for {}", ev.kind, ev.session_id)
            return
        key = runtime_session_key(ev.session_id, agent_id=ev.agent_id)
        q = self._queues.get(key)
        if q is None:
            q = asyncio.Queue()
            self._queues[key] = q
            self._session_ids[key] = (ev.session_id, ev.agent_id)
            self._workers[key] = asyncio.create_task(
                self._session_worker(key),
                name=f"event-persister:{key}",
            )
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:
            logger.exception("EventPersister queue full for {}", key)
            asyncio.create_task(self._persist_one(ev))

    async def flush(
        self,
        session_id: str,
        *,
        agent_id: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        key = runtime_session_key(session_id, agent_id=agent_id)
        q = self._queues.get(key)
        if q is None:
            return
        done = asyncio.Event()
        q.put_nowait(_FlushMarker(done))
        await asyncio.wait_for(done.wait(), timeout=timeout)

    async def aclose(self) -> None:
        self._closed = True
        for q in list(self._queues.values()):
            q.put_nowait(_SENTINEL)
        for task in list(self._workers.values()):
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(task, timeout=5.0)
        self._queues.clear()
        self._workers.clear()
        self._session_ids.clear()

    async def _session_worker(self, key: str) -> None:
        q = self._queues[key]
        session_id, agent_id = self._session_ids[key]
        try:
            while True:
                item = await q.get()
                if item is _SENTINEL:
                    break
                if isinstance(item, _FlushMarker):
                    item.done.set()
                    continue
                await self._handle_event(session_id, agent_id, item)
                if item.kind in ("run_complete", "run_cancelled"):
                    await self._drain_pending(q, session_id, agent_id)
                    break
        finally:
            self._queues.pop(key, None)
            self._workers.pop(key, None)
            self._session_ids.pop(key, None)

    async def _drain_pending(
        self,
        q: asyncio.Queue[Any],
        session_id: str,
        agent_id: str | None,
    ) -> None:
        while not q.empty():
            item = q.get_nowait()
            if isinstance(item, _FlushMarker):
                item.done.set()
            elif item is not _SENTINEL and isinstance(item, PersistEvent):
                await self._handle_event(session_id, agent_id, item)

    async def _handle_event(
        self,
        session_id: str,
        agent_id: str | None,
        ev: PersistEvent,
    ) -> None:
        lock = getattr(self._store, "lock", None)
        if callable(lock):
            async with lock(session_id, agent_id=agent_id):
                await self._persist_locked(session_id, agent_id, ev)
        else:
            await self._persist_locked(session_id, agent_id, ev)

    async def _persist_locked(
        self,
        session_id: str,
        agent_id: str | None,
        ev: PersistEvent,
    ) -> None:
        if ev.kind == "user_turn":
            if ev.user_message is None:
                return
            await self._store.append(
                session_id,
                [ev.user_message],
                agent_id=agent_id,
                user_id=ev.user_id,
            )
            return

        if ev.kind == "run_complete":
            messages = ev.new_messages or []
            to_append = incremental_append_messages(
                messages,
                skip_user_prompt=ev.skip_user_prompt,
            )
            if ev.repair_dangling_tool_calls:
                to_append = repair_unprocessed_tool_calls(to_append)
            if to_append:
                await self._store.append(
                    session_id,
                    to_append,
                    agent_id=agent_id,
                    user_id=ev.user_id,
                )
            return

        if ev.kind == "run_cancelled":
            if ev.result_messages:
                to_append = incremental_append_messages(
                    ev.result_messages,
                    skip_user_prompt=ev.skip_user_prompt,
                )
            elif ev.accumulator is not None:
                to_append = incremental_append_messages(
                    ev.accumulator.to_model_messages(include_tool_calls=ev.include_tool_calls),
                    skip_user_prompt=ev.skip_user_prompt,
                )
            else:
                to_append = []
            if ev.repair_dangling_tool_calls:
                to_append = repair_unprocessed_tool_calls(to_append)
            if to_append:
                await self._store.append(
                    session_id,
                    to_append,
                    agent_id=agent_id,
                    user_id=ev.user_id,
                )

    async def _persist_one(self, ev: PersistEvent) -> None:
        await self._handle_event(ev.session_id, ev.agent_id, ev)
