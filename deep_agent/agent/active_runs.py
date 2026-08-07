from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock

from pydantic_ai import CancellationToken


@dataclass
class ActiveRun:
    agent_id: str | None
    session_id: str
    user_id: str | None
    cancellation_token: CancellationToken
    started_at: datetime
    status: str = "running"


class ActiveRunRegistry:
    """Process-local registry for cancellable streaming Agent runs."""

    def __init__(self) -> None:
        self._runs: dict[tuple[str | None, str], ActiveRun] = {}
        self._lock = RLock()

    def register(
        self,
        *,
        agent_id: str | None,
        session_id: str,
        user_id: str | None,
    ) -> ActiveRun:
        key = (agent_id, session_id)
        entry = ActiveRun(
            agent_id=agent_id,
            session_id=session_id,
            user_id=user_id,
            cancellation_token=CancellationToken(),
            started_at=datetime.now(timezone.utc),
        )
        with self._lock:
            if key in self._runs:
                raise RuntimeError(f"Agent run already registered: {key!r}")
            self._runs[key] = entry
        return entry

    def unregister(self, entry: ActiveRun) -> None:
        key = (entry.agent_id, entry.session_id)
        with self._lock:
            if self._runs.get(key) is entry:
                self._runs.pop(key, None)

    def request_cancel(
        self,
        *,
        agent_id: str | None,
        session_id: str,
        user_id: str | None,
    ) -> bool:
        key = (agent_id, session_id)
        with self._lock:
            entry = self._runs.get(key)
            if entry is None or entry.user_id != user_id:
                return False
            entry.status = "cancel_requested"
            token = entry.cancellation_token
        token.cancel()
        return True

    def cancel_all(self) -> None:
        with self._lock:
            entries = list(self._runs.values())
            self._runs.clear()
        for entry in entries:
            entry.status = "cancel_requested"
            entry.cancellation_token.cancel()

    def get(self, *, agent_id: str | None, session_id: str) -> ActiveRun | None:
        with self._lock:
            return self._runs.get((agent_id, session_id))
