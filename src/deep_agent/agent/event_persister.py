from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any, Literal

from loguru import logger
from pydantic_ai.messages import ModelMessage, ModelRequest

from deep_agent.agent.history import (
    incremental_append_messages,
    mark_cancelled_response_interrupted,
    repair_unprocessed_tool_calls,
)
from deep_agent.db.conversation import SessionStore
from deep_agent.db.session_scope import runtime_session_key

_SENTINEL = object()
_WRITE_TIMEOUT = 10.0


@dataclass
class _FlushMarker:
    done: asyncio.Event


@dataclass(frozen=True)
class PersistEvent:
    session_id: str
    agent_id: str | None
    user_id: str | None
    kind: Literal["user_turn", "run_complete", "run_cancelled"]
    turn_id: str | None = None
    run_id: str | None = None
    app_id: str | None = None
    space_id: str | None = None
    user_message: ModelRequest | None = None
    new_messages: list[ModelMessage] | None = None
    skip_user_prompt: bool = False
    result_messages: list[ModelMessage] | None = None
    repair_dangling_tool_calls: bool = True
    terminal_status: str | None = None
    result_queue: asyncio.Queue[dict[str, Any]] | None = None


class EventPersister:
    """Background consumer: decouples SSE-cancelable runs from SessionStore writes."""

    def __init__(self, store: SessionStore) -> None:
        self._store = store
        self._queues: dict[str, asyncio.Queue[Any]] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._session_ids: dict[str, tuple[str, str | None]] = {}
        self._write_errors: dict[str, str] = {}
        self._closed = False

    async def start(self) -> None:
        return

    def publish(self, ev: PersistEvent) -> None:
        if self._closed:
            logger.warning("EventPersister closed, dropping {} for {}", ev.kind, ev.session_id)
            return
        key = runtime_session_key(ev.session_id, agent_id=ev.agent_id)
        if ev.kind == "user_turn":
            self._write_errors.pop(key, None)
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
        ignore_errors: bool = False,
    ) -> None:
        key = runtime_session_key(session_id, agent_id=agent_id)
        # Deletion still drains queued writes, but a past failed write must not
        # make an otherwise deletable session permanently impossible to remove.
        if not ignore_errors:
            self._raise_write_error(key)
        q = self._queues.get(key)
        if q is None:
            return
        worker = self._workers.get(key)
        if worker is None:
            return
        done = asyncio.Event()
        q.put_nowait(_FlushMarker(done))
        marker_waiter = asyncio.create_task(done.wait())
        try:
            completed, _ = await asyncio.wait(
                {marker_waiter, worker},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not completed:
                raise TimeoutError
            if worker in completed:
                await worker
            else:
                await marker_waiter
            if not ignore_errors:
                self._raise_write_error(key)
        finally:
            if not marker_waiter.done():
                marker_waiter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await marker_waiter

    def _raise_write_error(self, key: str) -> None:
        error = self._write_errors.get(key)
        if error is not None:
            raise RuntimeError(f"Session history persistence failed ({error})")

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
        self._write_errors.clear()

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
        except Exception as exc:
            # Keep a bounded failure receipt after removing the finished worker;
            # a subsequent flush must not falsely acknowledge a failed write.
            if len(self._write_errors) >= 128:
                self._write_errors.pop(next(iter(self._write_errors)))
            self._write_errors[key] = type(exc).__name__
            logger.error("Session history persistence failed session={} error={}",
                         session_id, type(exc).__name__)
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
                async with asyncio.timeout(_WRITE_TIMEOUT):
                    await self._persist_locked(session_id, agent_id, ev)
        else:
            async with asyncio.timeout(_WRITE_TIMEOUT):
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
            rows = await self._store.append(
                session_id,
                [ev.user_message],
                agent_id=agent_id,
                user_id=ev.user_id,
                app_id=ev.app_id,
                space_id=ev.space_id,
                turn_id=ev.turn_id,
                run_id=ev.run_id,
                first_message_ordinal=0,
            )
            await self._publish_rows(ev, rows)
            return

        if ev.kind == "run_complete":
            messages = ev.new_messages or []
            to_append = incremental_append_messages(
                messages,
                skip_user_prompt=ev.skip_user_prompt,
            )
            if ev.repair_dangling_tool_calls:
                to_append = repair_unprocessed_tool_calls(to_append, diagnostic_fields={
                    "session_id": session_id, "run_id": ev.run_id, "agent_id": agent_id,
                    "phase": "terminal_persistence",
                })
            if to_append:
                rows = await self._store.append(
                    session_id,
                    to_append,
                    agent_id=agent_id,
                    user_id=ev.user_id,
                    app_id=ev.app_id,
                    space_id=ev.space_id,
                    turn_id=ev.turn_id,
                    run_id=ev.run_id,
                    first_message_ordinal=1 if ev.skip_user_prompt else 0,
                    terminal_status=ev.terminal_status,
                )
                await self._publish_rows(ev, rows)
            return

        if ev.kind == "run_cancelled":
            to_append = incremental_append_messages(
                mark_cancelled_response_interrupted(ev.result_messages or []),
                skip_user_prompt=ev.skip_user_prompt,
            )
            if to_append:
                rows = await self._store.append(
                    session_id,
                    to_append,
                    agent_id=agent_id,
                    user_id=ev.user_id,
                    app_id=ev.app_id,
                    space_id=ev.space_id,
                    turn_id=ev.turn_id,
                    run_id=ev.run_id,
                    first_message_ordinal=1 if ev.skip_user_prompt else 0,
                    terminal_status="interrupted",
                )
                await self._publish_rows(ev, rows)

    async def _publish_rows(self, ev: PersistEvent, rows: list[Any]) -> None:
        if ev.result_queue is None:
            return
        for row in rows:
            ev.result_queue.put_nowait(
                {
                    "type": "session_event_appended",
                    "persisted": True,
                    "event": row.public_dict(),
                }
            )

    async def _persist_one(self, ev: PersistEvent) -> None:
        await self._handle_event(ev.session_id, ev.agent_id, ev)
