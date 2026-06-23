from __future__ import annotations

from abc import abstractmethod
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import ChatSession
from deep_agent.db.stores._session import SessionBoundMixin


class ChatSessionStoreBase(SessionBoundMixin):
    async def list_session_keys(self) -> list[str]:
        async with self._read() as db:
            return await self._list_session_keys(db)

    async def get_agent_id(self, session_key: str) -> str | None:
        async with self._read() as db:
            return await self._get_agent_id(db, session_key)

    async def get_timestamps(self, session_key: str) -> tuple[datetime | None, datetime | None]:
        async with self._read() as db:
            return await self._get_timestamps(db, session_key)

    async def delete(self, session_key: str) -> None:
        async with self._write() as db:
            await self._delete(db, session_key)

    @abstractmethod
    async def _upsert(
        self,
        db: AsyncSession,
        session_key: str,
        *,
        agent_id: str | None,
        user_id: str | None,
        now: datetime,
    ) -> None:
        """Dialect-specific upsert for ChatSession."""

    async def _list_session_keys(self, db: AsyncSession) -> list[str]:
        result = await db.execute(
            select(ChatSession.session_key).order_by(ChatSession.updated_at.desc())
        )
        return list(result.scalars().all())

    async def _get_agent_id(self, db: AsyncSession, session_key: str) -> str | None:
        result = await db.execute(
            select(ChatSession.agent_id).where(ChatSession.session_key == session_key)
        )
        return result.scalar_one_or_none()

    async def _get_timestamps(
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

    async def _delete(self, db: AsyncSession, session_key: str) -> None:
        await db.execute(delete(ChatSession).where(ChatSession.session_key == session_key))
