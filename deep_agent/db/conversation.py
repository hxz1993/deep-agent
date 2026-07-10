from __future__ import annotations

import asyncio
import contextlib
import sys
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from pydantic_ai.messages import ModelMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.agent.history import first_user_prompt
from deep_agent.db.session_list import SessionListFilters, SessionSummaryRow
from deep_agent.db.session_scope import normalize_agent_scope, runtime_session_key
from deep_agent.db.stores.chat_session.base import ChatSessionStoreBase
from deep_agent.db.stores.message.base import MessageStoreBase

__all__ = [
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

    async def truncate_history(
        self,
        session_id: str,
        *,
        agent_id: str | None = None,
        keep_count: int,
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

    async def get_session_title(
        self, session_id: str, *, agent_id: str | None = None
    ) -> str | None:
        """获取会话标题，无标题返回 None"""
        ...

    async def update_session_title(
        self, session_id: str, agent_id: str | None, title: str
    ) -> None:
        """更新会话标题"""
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
        db = self._session_factory()
        await db.__aenter__()
        exc_info: tuple[type[BaseException] | None, BaseException | None, Any] = (
            None,
            None,
            None,
        )
        try:
            try:
                yield db
                await asyncio.shield(db.commit())
            except BaseException:
                exc_info = sys.exc_info()
                with contextlib.suppress(Exception):
                    await asyncio.shield(db.rollback())
                raise
        finally:
            with contextlib.suppress(Exception):
                await asyncio.shield(db.__aexit__(*exc_info))

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
            next_sequence = await self._messages._get_max_sequence(db, scope, session_id) + 1
            existing_title = await self._chat_sessions.get_session_title(db, scope, session_id)
            # 仅首轮写入时 seed；避免迁移前无 title 的历史会话在后续轮次被误写。
            title = (
                first_user_prompt(messages)
                if existing_title is None and next_sequence == 1
                else None
            )
            await self._chat_sessions._upsert(
                db,
                scope,
                session_id,
                user_id=user_id,
                title=title,
                now=now,
            )
            await self._messages._append_messages(
                db,
                scope,
                session_id,
                messages,
                start_sequence=next_sequence,
            )

    async def truncate_history(
        self,
        session_id: str,
        *,
        agent_id: str | None = None,
        keep_count: int,
    ) -> None:
        scope = normalize_agent_scope(agent_id)
        async with self._write() as db:
            await self._messages._truncate_from_sequence(
                db,
                scope,
                session_id,
                keep_count=max(keep_count, 0),
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

    async def get_session_title(
        self, session_id: str, *, agent_id: str | None = None
    ) -> str | None:
        """获取会话标题。"""
        scope = normalize_agent_scope(agent_id)
        async with self._session_factory() as db:
            return await self._chat_sessions.get_session_title(db, scope, session_id)

    async def update_session_title(
        self, session_id: str, agent_id: str | None, title: str
    ) -> None:
        """更新会话标题。"""
        scope = normalize_agent_scope(agent_id)
        async with self._write() as db:
            await self._chat_sessions.update_session_title(db, scope, session_id, title)

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


# ponytail: backward-compat alias
SqlConversationStore = SqlConversationCoordinator
