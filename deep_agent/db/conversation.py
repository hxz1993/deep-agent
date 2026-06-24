from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from pydantic_ai.messages import ModelMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.session_list import MAX_LIST_LIMIT, SessionListFilters, SessionSummaryRow
from deep_agent.db.stores.chat_session.base import ChatSessionStoreBase
from deep_agent.db.stores.message.base import MessageStoreBase

__all__ = [
    "InMemoryConversationCoordinator",
    "InMemoryConversationStore",
    "InMemorySessionStore",
    "SessionStore",
    "SqlConversationCoordinator",
    "SqlConversationStore",
]


@runtime_checkable
class SessionStore(Protocol):
    """Persistence boundary for conversation history."""

    async def get_history(self, session_id: str) -> list[ModelMessage]:
        ...

    async def append(
        self,
        session_id: str,
        messages: list[ModelMessage],
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        ...

    async def list_sessions(self) -> list[str]:
        ...

    async def delete(self, session_id: str) -> None:
        ...

    async def list_session_summaries(
        self,
        filters: SessionListFilters,
    ) -> tuple[list[SessionSummaryRow], int]:
        ...


class SqlConversationCoordinator:
    """SQL-backed coordinator; orchestrates chat_session and message stores."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        chat_session: ChatSessionStoreBase,
        message: MessageStoreBase,
    ) -> None:
        self._session_factory = session_factory
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._chat_sessions = chat_session
        self._messages = message

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
        return await self._chat_sessions.list_summaries(
            agent_id=filters.agent_id,
            user_id=filters.user_id,
            limit=filters.limit,
            offset=filters.offset,
            updated_after=filters.updated_after,
            updated_before=filters.updated_before,
            has_messages=filters.has_messages,
            sort=filters.sort,
        )


class InMemoryConversationCoordinator:
    """Process-local coordinator for dev and single-process deployments."""

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

    async def list_session_summaries(
        self,
        filters: SessionListFilters,
    ) -> tuple[list[SessionSummaryRow], int]:
        from deep_agent.agent.history import first_user_prompt, session_activity_timestamps
        from deep_agent.db.session_key import parse_scoped_session_key

        limit = min(max(filters.limit, 1), MAX_LIST_LIMIT)
        offset = max(filters.offset, 0)
        rows: list[SessionSummaryRow] = []

        for session_key in self._histories:
            history = self._histories[session_key]
            parsed_agent_id, _ = parse_scoped_session_key(session_key)
            created_at, updated_at = session_activity_timestamps(history) if history else (None, None)

            if filters.agent_id is not None and parsed_agent_id != filters.agent_id:
                continue
            if filters.user_id is not None:
                continue
            if filters.updated_after is not None and (
                updated_at is None or updated_at < filters.updated_after
            ):
                continue
            if filters.updated_before is not None and (
                updated_at is None or updated_at > filters.updated_before
            ):
                continue
            message_count = len(history)
            if filters.has_messages is True and message_count == 0:
                continue
            if filters.has_messages is False and message_count > 0:
                continue

            rows.append(
                SessionSummaryRow(
                    session_key=session_key,
                    agent_id=parsed_agent_id,
                    agent_name=None,
                    created_at=created_at or datetime.min,
                    updated_at=updated_at or datetime.min,
                    message_count=message_count,
                    user_prompt=first_user_prompt(history),
                )
            )

        reverse = True
        if filters.sort == "updated_at":
            rows.sort(key=lambda row: row.updated_at, reverse=reverse)
        else:
            rows.sort(key=lambda row: row.created_at, reverse=reverse)

        total = len(rows)
        return rows[offset : offset + limit], total


# ponytail: backward-compat aliases for tests and external callers
SqlConversationStore = SqlConversationCoordinator
InMemoryConversationStore = InMemoryConversationCoordinator
InMemorySessionStore = InMemoryConversationCoordinator
