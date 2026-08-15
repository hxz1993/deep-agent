from __future__ import annotations

from datetime import datetime

from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import ChatSession
from deep_agent.db.stores.chat_session.base import ChatSessionStoreBase


class MySQLChatSessionStore(ChatSessionStoreBase):
    async def _upsert(
        self,
        db: AsyncSession,
        agent_scope: str,
        session_id: str,
        *,
        user_id: str | None,
        title: str | None = None,
        now: datetime,
    ) -> None:
        stmt = insert(ChatSession).values(
            agent_id=agent_scope,
            session_id=session_id,
            user_id=user_id,
            title=title,
            created_at=now,
            updated_at=now,
        )
        update_values = {"updated_at": now}
        if title is not None:
            update_values["title"] = title
        stmt = stmt.on_duplicate_key_update(**update_values)
        await db.execute(stmt)
