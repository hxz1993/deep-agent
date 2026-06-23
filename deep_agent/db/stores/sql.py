from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timezone

from pydantic_ai.messages import ModelMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.repositories.message import MessageRepository
from deep_agent.db.repositories.session_meta import SessionMetaRepository
from deep_agent.db.session_list import SessionListFilters, SessionSummaryRow


class SqlSessionStore(ABC):
    """Reusable SQL-backed session store base class."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._messages = MessageRepository()
        self._session_meta = SessionMetaRepository()
        from deep_agent.db.repositories.session import SessionRepository

        self._sessions = SessionRepository()

    def lock(self, session_key: str) -> asyncio.Lock:
        return self._locks[session_key]

    @abstractmethod
    async def _upsert_session(
        self,
        db: AsyncSession,
        session_key: str,
        *,
        agent_id: str | None,
        user_id: str | None,
        now: datetime,
    ) -> None:
        """Dialect-specific upsert for ChatSession."""

    async def get_history(self, session_key: str) -> list[ModelMessage]:
        async with self._session_factory() as db:
            return await self._messages.get_history(db, session_key)

    async def append(
        self,
        session_key: str,
        messages: list[ModelMessage],
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        if not messages:
            return

        async with self._session_factory() as db:
            now = datetime.now(timezone.utc)
            await self._upsert_session(
                db,
                session_key,
                agent_id=agent_id,
                user_id=user_id,
                now=now,
            )
            next_sequence = await self._messages.get_max_sequence(db, session_key) + 1
            await self._messages.append_messages(
                db,
                session_key,
                messages,
                start_sequence=next_sequence,
            )
            await db.commit()

    async def list_sessions(self) -> list[str]:
        async with self._session_factory() as db:
            return await self._session_meta.list_session_keys(db)

    async def get_session_agent_id(self, session_key: str) -> str | None:
        async with self._session_factory() as db:
            return await self._session_meta.get_agent_id(db, session_key)

    async def get_session_timestamps(self, session_key: str) -> tuple[datetime | None, datetime | None]:
        async with self._session_factory() as db:
            return await self._session_meta.get_timestamps(db, session_key)

    async def delete(self, session_key: str) -> None:
        async with self._session_factory() as db:
            await self._session_meta.delete(db, session_key)
            await db.commit()
        self._locks.pop(session_key, None)

    async def list_session_summaries(
        self,
        filters: SessionListFilters,
    ) -> tuple[list[SessionSummaryRow], int]:
        async with self._session_factory() as db:
            return await self._sessions.list_summaries(
                db,
                agent_id=filters.agent_id,
                user_id=filters.user_id,
                limit=filters.limit,
                offset=filters.offset,
                updated_after=filters.updated_after,
                updated_before=filters.updated_before,
                has_messages=filters.has_messages,
                sort=filters.sort,
            )
