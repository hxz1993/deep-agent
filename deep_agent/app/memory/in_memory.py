from __future__ import annotations

import asyncio
from collections import defaultdict

from pydantic_ai.messages import ModelMessage


class InMemorySessionStore:
    """Process-local session store.

    Good enough for development and single-process deployments. Each session has
    its own lock so concurrent requests on the same session serialize their
    history reads/writes. Replace with a durable store for production.
    """

    def __init__(self) -> None:
        self._histories: dict[str, list[ModelMessage]] = defaultdict(list)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def lock(self, session_id: str) -> asyncio.Lock:
        return self._locks[session_id]

    async def get_history(self, session_id: str) -> list[ModelMessage]:
        return list(self._histories.get(session_id, []))

    async def append(
        self,
        session_id: str,
        messages: list[ModelMessage],
        *,
        agent_id: object | None = None,
        user_id: str | None = None,
    ) -> None:
        del agent_id, user_id
        if not messages:
            return
        self._histories[session_id].extend(messages)

    async def list_sessions(self) -> list[str]:
        return list(self._histories.keys())

    async def delete(self, session_id: str) -> None:
        self._histories.pop(session_id, None)
        self._locks.pop(session_id, None)
