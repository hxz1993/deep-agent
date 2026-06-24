from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from pydantic_ai.messages import ModelMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.session_list import MAX_LIST_LIMIT, SessionListFilters, SessionSummaryRow
from deep_agent.db.session_scope import NO_AGENT_SCOPE, agent_scope_to_api, normalize_agent_scope, runtime_session_key
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

    async def get_history(self, session_id: str, *, agent_id: str | None = None) -> list[ModelMessage]:
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

    async def list_sessions(self) -> list[tuple[str, str]]:
        ...

    async def resolve_ref(
        self,
        session_id: str,
        *,
        agent_id: str | None = None,
    ) -> tuple[str, str] | None:
        ...

    async def delete(self, session_id: str, *, agent_id: str | None = None) -> None:
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

    def lock(self, session_id: str, *, agent_id: str | None = None) -> asyncio.Lock:
        return self._locks[runtime_session_key(session_id, agent_id=agent_id)]

    @asynccontextmanager
    async def _write(self):
        async with self._session_factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def get_history(self, session_id: str, *, agent_id: str | None = None) -> list[ModelMessage]:
        scope = normalize_agent_scope(agent_id)
        return await self._messages.get_history(scope, session_id)

    async def append(
        self,
        session_id: str,
        messages: list[ModelMessage],
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        if not messages:
            return

        scope = normalize_agent_scope(agent_id)
        async with self._write() as db:
            now = datetime.now(timezone.utc)
            await self._chat_sessions._upsert(
                db,
                scope,
                session_id,
                user_id=user_id,
                now=now,
            )
            next_sequence = await self._messages._get_max_sequence(db, scope, session_id) + 1
            await self._messages._append_messages(
                db,
                scope,
                session_id,
                messages,
                start_sequence=next_sequence,
            )

    async def list_sessions(self) -> list[tuple[str, str]]:
        return await self._chat_sessions.list_sessions()

    async def resolve_ref(
        self,
        session_id: str,
        *,
        agent_id: str | None = None,
    ) -> tuple[str, str] | None:
        return await self._chat_sessions.resolve_ref(session_id, agent_id=agent_id)

    async def get_session_timestamps(
        self,
        session_id: str,
        *,
        agent_id: str | None = None,
    ) -> tuple[datetime | None, datetime | None]:
        scope = normalize_agent_scope(agent_id)
        return await self._chat_sessions.get_timestamps(scope, session_id)

    async def delete(self, session_id: str, *, agent_id: str | None = None) -> None:
        scope = normalize_agent_scope(agent_id)
        await self._chat_sessions.delete(scope, session_id)
        self._locks.pop(runtime_session_key(session_id, agent_id=agent_id), None)

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
        self._histories: dict[tuple[str, str], list[ModelMessage]] = defaultdict(list)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def lock(self, session_id: str, *, agent_id: str | None = None) -> asyncio.Lock:
        return self._locks[runtime_session_key(session_id, agent_id=agent_id)]

    async def get_history(self, session_id: str, *, agent_id: str | None = None) -> list[ModelMessage]:
        scope = normalize_agent_scope(agent_id)
        return list(self._histories.get((scope, session_id), []))

    async def append(
        self,
        session_id: str,
        messages: list[ModelMessage],
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        del user_id
        if not messages:
            return
        scope = normalize_agent_scope(agent_id)
        self._histories[(scope, session_id)].extend(messages)

    async def list_sessions(self) -> list[tuple[str, str]]:
        return list(self._histories.keys())

    async def resolve_ref(
        self,
        session_id: str,
        *,
        agent_id: str | None = None,
    ) -> tuple[str, str] | None:
        scope = normalize_agent_scope(agent_id)
        sessions = list(self._histories.keys())
        target = (scope, session_id)
        if target in sessions:
            return target

        matches = [ref for ref in sessions if ref[1] == session_id]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        if scope != NO_AGENT_SCOPE:
            for ref in matches:
                if ref[0] == scope:
                    return ref
        return None

    async def delete(self, session_id: str, *, agent_id: str | None = None) -> None:
        scope = normalize_agent_scope(agent_id)
        self._histories.pop((scope, session_id), None)
        self._locks.pop(runtime_session_key(session_id, agent_id=agent_id), None)

    async def list_session_summaries(
        self,
        filters: SessionListFilters,
    ) -> tuple[list[SessionSummaryRow], int]:
        from deep_agent.agent.history import first_user_prompt, session_activity_timestamps

        limit = min(max(filters.limit, 1), MAX_LIST_LIMIT)
        offset = max(filters.offset, 0)
        rows: list[SessionSummaryRow] = []

        for agent_scope, session_id in self._histories:
            history = self._histories[(agent_scope, session_id)]
            api_agent_id = agent_scope_to_api(agent_scope)
            created_at, updated_at = session_activity_timestamps(history) if history else (None, None)

            if filters.agent_id is not None and api_agent_id != filters.agent_id:
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
                    session_id=session_id,
                    agent_id=api_agent_id,
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
