from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import ChatSession


class SessionMetaRepository:
    async def list_session_keys(self, db: AsyncSession) -> list[str]:
        result = await db.execute(
            select(ChatSession.session_key).order_by(ChatSession.updated_at.desc())
        )
        return list(result.scalars().all())

    async def get_agent_id(self, db: AsyncSession, session_key: str) -> str | None:
        result = await db.execute(
            select(ChatSession.agent_id).where(ChatSession.session_key == session_key)
        )
        return result.scalar_one_or_none()

    async def get_timestamps(
        self,
        db: AsyncSession,
        session_key: str,
    ) -> tuple[datetime | None, datetime | None]:
        result = await db.execute(
            select(ChatSession.created_at, ChatSession.updated_at).where(
                ChatSession.session_key == session_key
            )
        )
        row = result.one_or_none()
        if row is None:
            return None, None
        return row.created_at, row.updated_at

    async def delete(self, db: AsyncSession, session_key: str) -> None:
        await db.execute(delete(ChatSession).where(ChatSession.session_key == session_key))
