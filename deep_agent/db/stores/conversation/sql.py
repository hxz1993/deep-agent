from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from pydantic_ai.messages import ModelMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.session_list import SessionListFilters, SessionSummaryRow
from deep_agent.db.stores.chat_session.base import ChatSessionStoreBase
from deep_agent.db.stores.message.base import MessageStoreBase
from deep_agent.db.stores.session_query.base import SessionQueryStoreBase


class SqlConversationStore:
    """SQL-backed conversation store; orchestrates chat_session, message, session_query."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        chat_session: ChatSessionStoreBase,
        message: MessageStoreBase,
        session_query: SessionQueryStoreBase,
    ) -> None:
        self._session_factory = session_factory
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._chat_sessions = chat_session
        self._messages = message
        self._session_query = session_query

    def lock(self, session_key: str) -> asyncio.Lock:
        return self._locks[session_key]

    @asynccontextmanager
    async def _write(self):
        async with self._session_factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def get_history(self, session_key: str) -> list[ModelMessage]:
        return await self._messages.get_history(session_key)

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

        async with self._write() as db:
            now = datetime.now(timezone.utc)
            await self._chat_sessions._upsert(
                db,
                session_key,
                agent_id=agent_id,
                user_id=user_id,
                now=now,
            )
            next_sequence = await self._messages._get_max_sequence(db, session_key) + 1
            await self._messages._append_messages(
                db,
                session_key,
                messages,
                start_sequence=next_sequence,
            )

    async def list_sessions(self) -> list[str]:
        return await self._chat_sessions.list_session_keys()

    async def get_session_agent_id(self, session_key: str) -> str | None:
        return await self._chat_sessions.get_agent_id(session_key)

    async def get_session_timestamps(self, session_key: str) -> tuple[datetime | None, datetime | None]:
        return await self._chat_sessions.get_timestamps(session_key)

    async def delete(self, session_key: str) -> None:
        await self._chat_sessions.delete(session_key)
        self._locks.pop(session_key, None)

    async def list_session_summaries(
        self,
        filters: SessionListFilters,
    ) -> tuple[list[SessionSummaryRow], int]:
        return await self._session_query.list_summaries(
            agent_id=filters.agent_id,
            user_id=filters.user_id,
            limit=filters.limit,
            offset=filters.offset,
            updated_after=filters.updated_after,
            updated_before=filters.updated_before,
            has_messages=filters.has_messages,
            sort=filters.sort,
        )
