from __future__ import annotations

from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from deep_agent.db.stores.chat_session.base import ChatSessionStoreBase


class MySQLChatSessionStore(ChatSessionStoreBase):
    async def _upsert(
        self, db: AsyncSession, agent_scope: str, session_id: str, *,
        user_id: str | None, title: str | None = None, now: datetime,
    ) -> None:
        if user_id is None:
            raise ValueError("Session requires user_id")
        await self._ensure(db, session_id, agent_id=agent_scope,
                           user_id=user_id, title=title, now=now)
