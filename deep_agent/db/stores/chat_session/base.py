from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Select, delete, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.models import Agent, ChatSession, ChatSessionEvent
from deep_agent.db.session_list import MAX_LIST_LIMIT, SessionSummaryRow, SortField
from deep_agent.db.session_scope import SessionOwner, agent_scope_to_api
from deep_agent.db.stores._session import SessionBoundMixin


class ChatSessionStoreBase(SessionBoundMixin):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(session_factory)

    async def _ensure(
        self, db: AsyncSession, session_id: str, *, agent_id: str, user_id: str,
        app_id: str | None = None, space_id: str | None = None,
        title: str | None = None, now: datetime | None = None,
    ) -> ChatSession:
        timestamp = now or datetime.now(timezone.utc)
        row = (
            await db.execute(
                select(ChatSession).where(ChatSession.session_id == session_id).with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            row = ChatSession(
                session_id=session_id, agent_id=agent_id, user_id=user_id,
                app_id=app_id, space_id=space_id, title=title, next_sequence=1,
                created_at=timestamp, updated_at=timestamp,
            )
            db.add(row)
            await db.flush()
            return row
        if row.agent_id != agent_id or row.user_id != user_id:
            raise PermissionError("Session belongs to another Agent or user")
        if title is not None and row.title is None:
            row.title = title
        row.updated_at = timestamp
        return row

    async def list_sessions(self) -> list[tuple[str, str]]:
        async with self._read() as db:
            result = await db.execute(
                select(ChatSession.agent_id, ChatSession.session_id)
                .order_by(ChatSession.updated_at.desc())
            )
            return list(result.all())

    async def resolve_ref(
        self, session_id: str, *, agent_id: str | None = None,
        user_id: str | None = None,
    ) -> tuple[str, str] | None:
        stmt = select(ChatSession.agent_id, ChatSession.session_id).where(
            ChatSession.session_id == session_id
        )
        if agent_id is not None:
            stmt = stmt.where(ChatSession.agent_id == agent_id)
        if user_id is not None:
            stmt = stmt.where(ChatSession.user_id == user_id)
        async with self._read() as db:
            row = (await db.execute(stmt)).one_or_none()
        return (row.agent_id, row.session_id) if row is not None else None

    async def get_owner(self, agent_id: str, session_id: str) -> SessionOwner:
        async with self._read() as db:
            return await self._get_owner(db, agent_id, session_id)

    async def _get_owner(
        self, db: AsyncSession, agent_id: str, session_id: str,
        *, for_update: bool = False,
    ) -> SessionOwner:
        stmt = select(ChatSession.user_id).where(
            ChatSession.session_id == session_id, ChatSession.agent_id == agent_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        user_id = (await db.execute(stmt)).scalar_one_or_none()
        return SessionOwner(exists=user_id is not None, user_id=user_id)

    async def get_timestamps(
        self, agent_id: str, session_id: str,
    ) -> tuple[datetime | None, datetime | None]:
        async with self._read() as db:
            row = (
                await db.execute(
                    select(ChatSession.created_at, ChatSession.updated_at).where(
                        ChatSession.session_id == session_id,
                        ChatSession.agent_id == agent_id,
                    )
                )
            ).one_or_none()
        return (row.created_at, row.updated_at) if row is not None else (None, None)

    async def _delete(self, db: AsyncSession, agent_id: str, session_id: str) -> None:
        await db.execute(
            delete(ChatSession).where(
                ChatSession.session_id == session_id,
                ChatSession.agent_id == agent_id,
            )
        )

    async def get_session_title(
        self, db: AsyncSession, agent_id: str, session_id: str,
    ) -> str | None:
        return (
            await db.execute(
                select(ChatSession.title).where(
                    ChatSession.session_id == session_id,
                    ChatSession.agent_id == agent_id,
                )
            )
        ).scalar_one_or_none()

    async def update_session_title(
        self, db: AsyncSession, agent_id: str, session_id: str, title: str,
    ) -> None:
        await db.execute(
            update(ChatSession)
            .where(
                ChatSession.session_id == session_id,
                ChatSession.agent_id == agent_id,
            )
            .values(title=title, updated_at=datetime.now(timezone.utc))
        )

    async def update_session_title_if_current(
        self, db: AsyncSession, agent_id: str, session_id: str,
        *, expected_title: str | None, title: str,
    ) -> bool:
        condition = (
            ChatSession.title.is_(None)
            if expected_title is None
            else ChatSession.title == expected_title
        )
        result = await db.execute(
            update(ChatSession)
            .where(
                ChatSession.session_id == session_id,
                ChatSession.agent_id == agent_id,
                condition,
            )
            .values(title=title, updated_at=datetime.now(timezone.utc))
        )
        return bool(result.rowcount)

    @staticmethod
    def _apply_session_filters(
        stmt: Select, *, agent_id: str | None, user_id: str | None,
        updated_after: datetime | None, updated_before: datetime | None,
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

    async def list_summaries(
        self, *, agent_id: str | None = None, user_id: str | None = None,
        limit: int = 50, offset: int = 0,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        has_messages: bool | None = None, sort: SortField = "updated_at",
    ) -> tuple[list[SessionSummaryRow], int]:
        limit = min(max(limit, 1), MAX_LIST_LIMIT)
        offset = max(offset, 0)
        message_count = func.count(ChatSessionEvent.event_id)
        stmt = (
            select(
                ChatSession.session_id, ChatSession.agent_id, ChatSession.title,
                ChatSession.created_at, ChatSession.updated_at,
                Agent.name.label("agent_name"), message_count.label("message_count"),
            )
            .select_from(ChatSession)
            .join(Agent, ChatSession.agent_id == Agent.id)
            .outerjoin(
                ChatSessionEvent,
                (ChatSessionEvent.session_id == ChatSession.session_id)
                & ChatSessionEvent.event_type.in_(
                    ("user/message", "assistant/message", "tool/results", "model/request")
                ),
            )
            .group_by(
                ChatSession.session_id, ChatSession.agent_id, ChatSession.title,
                ChatSession.created_at, ChatSession.updated_at, Agent.name,
            )
        )
        stmt = self._apply_session_filters(
            stmt, agent_id=agent_id, user_id=user_id,
            updated_after=updated_after, updated_before=updated_before,
        )
        if has_messages is True:
            stmt = stmt.having(message_count > 0)
        elif has_messages is False:
            stmt = stmt.having(message_count == 0)
        order_column = ChatSession.updated_at if sort == "updated_at" else ChatSession.created_at
        stmt = stmt.order_by(order_column.desc()).limit(limit).offset(offset)
        async with self._read() as db:
            rows = list((await db.execute(stmt)).all())
            prompts = await self._first_user_prompts(db, [str(row.session_id) for row in rows])
            count_stmt = self._apply_session_filters(
                select(func.count()).select_from(ChatSession),
                agent_id=agent_id, user_id=user_id,
                updated_after=updated_after, updated_before=updated_before,
            )
            message_exists = exists(
                select(ChatSessionEvent.event_id).where(
                    ChatSessionEvent.session_id == ChatSession.session_id,
                    ChatSessionEvent.event_type.in_(
                        ("user/message", "assistant/message", "tool/results", "model/request")
                    ),
                )
            )
            if has_messages is True:
                count_stmt = count_stmt.where(message_exists)
            elif has_messages is False:
                count_stmt = count_stmt.where(~message_exists)
            total = int((await db.execute(count_stmt)).scalar_one())
        summaries = [
            SessionSummaryRow(
                session_id=row.session_id, agent_id=agent_scope_to_api(row.agent_id),
                agent_name=row.agent_name, created_at=row.created_at,
                updated_at=row.updated_at, message_count=int(row.message_count),
                user_prompt=prompts.get(row.session_id), title=row.title,
            )
            for row in rows
        ]
        return summaries, total

    async def _first_user_prompts(
        self, db: AsyncSession, session_ids: list[str],
    ) -> dict[str, str | None]:
        from deep_agent.agent.attachments import is_attachment_ref
        from deep_agent.agent.context_references import (
            context_reference_title,
            context_references_from_content,
        )

        if not session_ids:
            return {}
        rows = (
            await db.execute(
                select(ChatSessionEvent.session_id, ChatSessionEvent.sequence, ChatSessionEvent.payload)
                .where(
                    ChatSessionEvent.session_id.in_(session_ids),
                    ChatSessionEvent.event_type == "user/message",
                )
                .order_by(ChatSessionEvent.session_id, ChatSessionEvent.sequence)
            )
        ).all()
        result: dict[str, str | None] = {}
        for session_id, _sequence, payload in rows:
            if session_id in result:
                continue
            message = payload.get("message") if isinstance(payload, dict) else None
            parts = message.get("parts") if isinstance(message, dict) else None
            part = next(
                (
                    item for item in parts or []
                    if isinstance(item, dict) and item.get("part_kind") == "user-prompt"
                ),
                None,
            )
            content = part.get("content") if isinstance(part, dict) else None
            if isinstance(content, str):
                result[session_id] = content
            elif isinstance(content, list):
                text_value = next(
                    (item for item in content if isinstance(item, str) and item.strip()),
                    None,
                )
                if text_value is not None:
                    result[session_id] = text_value
                    continue
                attachments = [item for item in content if is_attachment_ref(item)]
                if attachments:
                    result[session_id] = "[附件] " + ", ".join(
                        str(item.get("name") or item["id"]) for item in attachments
                    )
                    continue
                refs = context_references_from_content(content)
                result[session_id] = context_reference_title(refs) if refs else None
            else:
                result[session_id] = None
        return result
