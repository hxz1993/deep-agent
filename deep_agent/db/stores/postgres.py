from __future__ import annotations

from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import ChatSession
from deep_agent.db.stores.sql import SqlSessionStore


class PostgresSessionStore(SqlSessionStore):
    """PostgreSQL-backed session store."""

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
        stmt = stmt.on_conflict_do_update(
            index_elements=[ChatSession.session_key],
            set_={
                "updated_at": now,
                **({"user_id": user_id} if user_id is not None else {}),
            },
        )
        await db.execute(stmt)
