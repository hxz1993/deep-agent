from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from threading import RLock

from pydantic_ai import CancellationToken

from deep_agent.agent.observability import log_event


@dataclass
class ActiveRun:
    agent_id: str | None
    session_id: str
    user_id: str | None
    cancellation_token: CancellationToken
    started_at: datetime
    conversation_id: str | None = None
    run_id: str | None = None
    status: str = "running"
    # Diagnostic only: never drives cancellation or persistence decisions.
    cancel_reason: str | None = None
    channel_account_id: str | None = None
    request_owned: bool = False
    finished: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def observe_cancellation(self, reason: str) -> None:
        if self.cancel_reason is None:
            self.cancel_reason = reason
            try:
                log_event("agent_cancel_observed", session_id=self.session_id,
                          agent_id=self.agent_id, run_id=self.run_id,
                          conversation_id=self.conversation_id, reason=reason)
            except Exception:
                pass


class ActiveRunRegistry:
    """Process-local registry for cancellable streaming Agent runs."""

    def __init__(self) -> None:
        self._runs: dict[tuple[str | None, str], ActiveRun] = {}
        self._lock = RLock()
        self._deletion_lock = asyncio.Lock()
        self._deletion_scope: tuple[str | None, str | None, str | None] | None = None
        self.generation = 0
        self._deletion_versions: dict[tuple, int] = {}

    def register(
        self,
        *,
        agent_id: str | None,
        session_id: str,
        user_id: str | None,
        conversation_id: str | None = None,
        run_id: str | None = None,
        channel_account_id: str | None = None,
        expected_generation: int | None = None,
        request_owned: bool = False,
    ) -> ActiveRun:
        key = (agent_id, session_id)
        entry = ActiveRun(
            agent_id=agent_id,
            session_id=session_id,
            user_id=user_id,
            cancellation_token=CancellationToken(),
            started_at=datetime.now(timezone.utc),
            conversation_id=conversation_id,
            run_id=run_id,
            channel_account_id=channel_account_id,
            request_owned=request_owned,
        )
        with self._lock:
            relevant_scopes = ((agent_id, None, None), (agent_id, session_id, None),
                               (None, session_id, None), (None, None, channel_account_id))
            if (expected_generation is not None and any(
                self._deletion_versions.get(scope, 0) > expected_generation
                for scope in relevant_scopes
            )) or (
                self._deletion_scope is not None and self._matches(entry, self._deletion_scope)
            ):
                raise RuntimeError("资源正在删除或已发生变化，请刷新后重试")
            if key in self._runs:
                raise RuntimeError(f"Agent run already registered: {key!r}")
            self._runs[key] = entry
        return entry

    def unregister(self, entry: ActiveRun) -> None:
        key = (entry.agent_id, entry.session_id)
        with self._lock:
            if self._runs.get(key) is entry:
                self._runs.pop(key, None)
                entry.finished.set()

    @staticmethod
    def _matches(entry: ActiveRun, scope) -> bool:
        agent_id, session_id, channel_id = scope
        return ((agent_id is None or entry.agent_id == agent_id)
                and (session_id is None or entry.session_id == session_id)
                and (channel_id is None or entry.channel_account_id == channel_id))

    @asynccontextmanager
    async def deleting(self, *, agent_id=None, session_id=None, channel_account_id=None, timeout=15.0):
        """Fence admission and await actual run cleanup without holding DB locks."""
        async with self._deletion_lock:
            scope = (agent_id, session_id, channel_account_id)
            with self._lock:
                self.generation += 1
                self._deletion_versions[scope] = self.generation
                self._deletion_scope = scope
                entries = [entry for entry in self._runs.values() if self._matches(entry, scope)]
            try:
                for entry in entries:
                    entry.status = "cancel_requested"
                    entry.observe_cancellation("resource_delete")
                    entry.cancellation_token.cancel()
                if entries:
                    await asyncio.wait_for(asyncio.gather(*(entry.finished.wait() for entry in entries)), timeout)
                yield entries
            finally:
                with self._lock:
                    self.generation += 1
                    self._deletion_versions[scope] = self.generation
                    self._deletion_scope = None

    def request_cancel(
        self,
        *,
        agent_id: str | None,
        session_id: str,
        user_id: str | None,
        reason: str = "cancel_api",
    ) -> bool:
        key = (agent_id, session_id)
        with self._lock:
            entry = self._runs.get(key)
            if entry is None or entry.user_id != user_id:
                return False
            entry.status = "cancel_requested"
            entry.observe_cancellation(reason)
            token = entry.cancellation_token
        token.cancel()
        return True

    def cancel_all(self) -> None:
        with self._lock:
            entries = list(self._runs.values())
            self._runs.clear()
        for entry in entries:
            entry.status = "cancel_requested"
            entry.observe_cancellation("server_shutdown")
            entry.cancellation_token.cancel()

    def get(self, *, agent_id: str | None, session_id: str) -> ActiveRun | None:
        with self._lock:
            return self._runs.get((agent_id, session_id))

    def active_count(self) -> int:
        with self._lock:
            return len(self._runs)

    def snapshot(self) -> tuple[ActiveRun, ...]:
        """Read existing registrations without changing execution ownership."""
        with self._lock:
            return tuple(replace(entry) for entry in self._runs.values())
