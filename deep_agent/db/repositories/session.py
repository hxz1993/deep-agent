from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import Agent, ChatMessage, ChatSession
from deep_agent.memory.history import first_user_prompt

_message_adapter: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)

EARLY_MESSAGE_LIMIT = 5
MAX_LIST_LIMIT = 200

SortField = Literal["updated_at", "created_at"]


@dataclass(frozen=True)
class SessionSummaryRow:
    session_key: str
    agent_id: str | None
    agent_name: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int
    user_prompt: str | None = None


class SessionRepository:
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
            stmt = stmt.where(ChatSession.agent_id == agent_id)
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
        session_keys: list[str],
    ) -> dict[str, str | None]:
        if not session_keys:
            return {}

        row_number = func.row_number().over(
            partition_by=ChatMessage.session_key,
            order_by=ChatMessage.sequence,
        ).label("rn")
        ranked = (
            select(
                ChatMessage.session_key.label("session_key"),
                ChatMessage.payload.label("payload"),
                row_number,
            )
            .where(ChatMessage.session_key.in_(session_keys))
            .subquery()
        )
        result = await db.execute(
            select(ranked.c.session_key, ranked.c.payload).where(ranked.c.rn <= EARLY_MESSAGE_LIMIT)
        )

        payloads_by_key: dict[str, list[ModelMessage]] = {}
        for session_key, payload in result.all():
            payloads_by_key.setdefault(session_key, []).append(
                _message_adapter.validate_python(payload)
            )

        return {
            session_key: first_user_prompt(messages)
            for session_key, messages in payloads_by_key.items()
        }

    async def list_summaries(
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
                ChatSession.session_key,
                ChatSession.agent_id,
                ChatSession.created_at,
                ChatSession.updated_at,
                Agent.name.label("agent_name"),
                func.count(ChatMessage.id).label("message_count"),
            )
            .select_from(ChatSession)
            .outerjoin(Agent, ChatSession.agent_id == Agent.id)
            .outerjoin(ChatMessage, ChatMessage.session_key == ChatSession.session_key)
            .group_by(
                ChatSession.session_key,
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
        session_keys = [row.session_key for row in rows]
        user_prompts = await self._fetch_early_user_prompts(db, session_keys)

        summaries = [
            SessionSummaryRow(
                session_key=row.session_key,
                agent_id=row.agent_id,
                agent_name=row.agent_name,
                created_at=row.created_at,
                updated_at=row.updated_at,
                message_count=int(row.message_count),
                user_prompt=user_prompts.get(row.session_key),
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
                select(ChatSession.session_key)
                .select_from(ChatSession)
                .outerjoin(ChatMessage, ChatMessage.session_key == ChatSession.session_key)
                .group_by(ChatSession.session_key)
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
