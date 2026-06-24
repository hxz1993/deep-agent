from __future__ import annotations

from abc import abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import Agent, ChatMessage, ChatSession
from deep_agent.db.session_list import (
    MAX_LIST_LIMIT,
    SessionSummaryRow,
    SortField,
)
from deep_agent.db.session_scope import NO_AGENT_SCOPE, agent_scope_to_api, normalize_agent_scope
from deep_agent.db.stores._session import SessionBoundMixin

if TYPE_CHECKING:
    from deep_agent.db.stores.message.base import MessageStoreBase


class ChatSessionStoreBase(SessionBoundMixin):
    def __init__(
        self,
        session_factory,
        *,
        message_store: MessageStoreBase | None = None,
    ) -> None:
        super().__init__(session_factory)
        self._messages = message_store

    async def list_sessions(self) -> list[tuple[str, str]]:
        async with self._read() as db:
            return await self._list_sessions(db)

    async def resolve_ref(
        self,
        session_id: str,
        *,
        agent_id: str | None = None,
    ) -> tuple[str, str] | None:
        async with self._read() as db:
            return await self._resolve_ref(db, session_id, agent_id=agent_id)

    async def get_timestamps(
        self,
        agent_scope: str,
        session_id: str,
    ) -> tuple[datetime | None, datetime | None]:
        async with self._read() as db:
            return await self._get_timestamps(db, agent_scope, session_id)

    async def delete(self, agent_scope: str, session_id: str) -> None:
        async with self._write() as db:
            await self._delete(db, agent_scope, session_id)

    async def list_summaries(
        self,
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        has_messages: bool | None = None,
        sort: SortField = "updated_at",
    ) -> tuple[list[SessionSummaryRow], int]:
        async with self._read() as db:
            return await self._list_summaries(
                db,
                agent_id=agent_id,
                user_id=user_id,
                limit=limit,
                offset=offset,
                updated_after=updated_after,
                updated_before=updated_before,
                has_messages=has_messages,
                sort=sort,
            )

    @abstractmethod
    async def _upsert(
        self,
        db: AsyncSession,
        agent_scope: str,
        session_id: str,
        *,
        user_id: str | None,
        now: datetime,
    ) -> None:
        """Dialect-specific upsert for ChatSession."""

    async def _list_sessions(self, db: AsyncSession) -> list[tuple[str, str]]:
        result = await db.execute(
            select(ChatSession.agent_id, ChatSession.session_id).order_by(ChatSession.updated_at.desc())
        )
        return list(result.all())

    async def _resolve_ref(
        self,
        db: AsyncSession,
        session_id: str,
        *,
        agent_id: str | None = None,
    ) -> tuple[str, str] | None:
        scope = normalize_agent_scope(agent_id)
        result = await db.execute(
            select(ChatSession.agent_id, ChatSession.session_id).where(
                ChatSession.session_id == session_id,
            )
        )
        matches = list(result.all())
        if not matches:
            return None

        target = (scope, session_id)
        for row in matches:
            if (row.agent_id, row.session_id) == target:
                return target
        if len(matches) == 1:
            return (matches[0].agent_id, matches[0].session_id)
        if scope != NO_AGENT_SCOPE:
            for row in matches:
                if row.agent_id == scope:
                    return (row.agent_id, row.session_id)
        return None

    async def _get_timestamps(
        self,
        db: AsyncSession,
        agent_scope: str,
        session_id: str,
    ) -> tuple[datetime | None, datetime | None]:
        result = await db.execute(
            select(ChatSession.created_at, ChatSession.updated_at).where(
                ChatSession.agent_id == agent_scope,
                ChatSession.session_id == session_id,
            )
        )
        row = result.one_or_none()
        if row is None:
            return None, None
        return row.created_at, row.updated_at

    async def _delete(self, db: AsyncSession, agent_scope: str, session_id: str) -> None:
        await db.execute(
            delete(ChatMessage).where(
                ChatMessage.agent_id == agent_scope,
                ChatMessage.session_id == session_id,
            )
        )
        await db.execute(
            delete(ChatSession).where(
                ChatSession.agent_id == agent_scope,
                ChatSession.session_id == session_id,
            )
        )

    @staticmethod
    def _apply_session_filters(
        stmt: Select,
        *,
        agent_id: str | None,
        user_id: str | None,
        updated_after: datetime | None,
        updated_before: datetime | None,
    ) -> Select:
        if agent_id is not None:
            stmt = stmt.where(ChatSession.agent_id == normalize_agent_scope(agent_id))
        if user_id is not None:
            stmt = stmt.where(ChatSession.user_id == user_id)
        if updated_after is not None:
            stmt = stmt.where(ChatSession.updated_at >= updated_after)
        if updated_before is not None:
            stmt = stmt.where(ChatSession.updated_at <= updated_before)
        return stmt

    @staticmethod
    def _order_column(sort: SortField):
        return ChatSession.updated_at if sort == "updated_at" else ChatSession.created_at

    async def _fetch_early_user_prompts(
        self,
        db: AsyncSession,
        session_refs: list[tuple[str, str]],
    ) -> dict[tuple[str, str], str | None]:
        from deep_agent.agent.history import first_user_prompt

        if self._messages is None:
            return {}
        payloads_by_ref = await self._messages._fetch_early_payloads(db, session_refs)
        return {
            session_ref: first_user_prompt(messages)
            for session_ref, messages in payloads_by_ref.items()
        }

    async def _list_summaries(
        self,
        db: AsyncSession,
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        has_messages: bool | None = None,
        sort: SortField = "updated_at",
    ) -> tuple[list[SessionSummaryRow], int]:
        limit = min(max(limit, 1), MAX_LIST_LIMIT)
        offset = max(offset, 0)

        summary_stmt = (
            select(
                ChatSession.session_id,
                ChatSession.agent_id,
                ChatSession.created_at,
                ChatSession.updated_at,
                Agent.name.label("agent_name"),
                func.count(ChatMessage.id).label("message_count"),
            )
            .select_from(ChatSession)
            .outerjoin(Agent, (ChatSession.agent_id == Agent.id) & (ChatSession.agent_id != NO_AGENT_SCOPE))
            .outerjoin(
                ChatMessage,
                (ChatMessage.agent_id == ChatSession.agent_id)
                & (ChatMessage.session_id == ChatSession.session_id),
            )
            .group_by(
                ChatSession.session_id,
                ChatSession.agent_id,
                ChatSession.created_at,
                ChatSession.updated_at,
                Agent.name,
            )
        )
        summary_stmt = self._apply_session_filters(
            summary_stmt,
            agent_id=agent_id,
            user_id=user_id,
            updated_after=updated_after,
            updated_before=updated_before,
        )
        if has_messages is True:
            summary_stmt = summary_stmt.having(func.count(ChatMessage.id) > 0)
        elif has_messages is False:
            summary_stmt = summary_stmt.having(func.count(ChatMessage.id) == 0)

        order_col = self._order_column(sort)
        summary_stmt = summary_stmt.order_by(order_col.desc()).limit(limit).offset(offset)

        rows = (await db.execute(summary_stmt)).all()
        session_refs = [(row.agent_id, row.session_id) for row in rows]
        user_prompts = await self._fetch_early_user_prompts(db, session_refs)

        summaries = [
            SessionSummaryRow(
                session_id=row.session_id,
                agent_id=agent_scope_to_api(row.agent_id),
                agent_name=row.agent_name,
                created_at=row.created_at,
                updated_at=row.updated_at,
                message_count=int(row.message_count),
                user_prompt=user_prompts.get((row.agent_id, row.session_id)),
            )
            for row in rows
        ]

        count_stmt = select(func.count()).select_from(ChatSession)
        count_stmt = self._apply_session_filters(
            count_stmt,
            agent_id=agent_id,
            user_id=user_id,
            updated_after=updated_after,
            updated_before=updated_before,
        )
        if has_messages is None:
            total = int((await db.execute(count_stmt)).scalar_one())
        else:
            grouped = (
                select(ChatSession.agent_id, ChatSession.session_id)
                .select_from(ChatSession)
                .outerjoin(
                    ChatMessage,
                    (ChatMessage.agent_id == ChatSession.agent_id)
                    & (ChatMessage.session_id == ChatSession.session_id),
                )
                .group_by(ChatSession.agent_id, ChatSession.session_id)
            )
            grouped = self._apply_session_filters(
                grouped,
                agent_id=agent_id,
                user_id=user_id,
                updated_after=updated_after,
                updated_before=updated_before,
            )
            if has_messages:
                grouped = grouped.having(func.count(ChatMessage.id) > 0)
            else:
                grouped = grouped.having(func.count(ChatMessage.id) == 0)
            total = int((await db.execute(select(func.count()).select_from(grouped.subquery()))).scalar_one())

        return summaries, total
