from __future__ import annotations

from datetime import datetime

from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import ChatSession
from deep_agent.db.stores.sql import SqlSessionStore


class MySQLSessionStore(SqlSessionStore):
    """MySQL-backed session store."""

    async def _upsert_session(
        self,
        db: AsyncSession,
        session_key: str,
        *,
        agent_id: str | None,
        user_id: str | None,
        now: datetime,
    ) -> None:
        stmt = insert(ChatSession).values(
            session_key=session_key,
            agent_id=agent_id,
            user_id=user_id,
            created_at=now,
            updated_at=now,
        )
        update_values = {"updated_at": now}
        if user_id is not None:
            update_values["user_id"] = user_id
        stmt = stmt.on_duplicate_key_update(**update_values)
        await db.execute(stmt)
