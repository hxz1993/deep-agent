from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from deep_agent.db.models import ChatSession
from deep_agent.memory.sql import SqlSessionStore


def scoped_session_key(agent_id: UUID, session_id: str) -> str:
    return f"{agent_id}:{session_id}"


def parse_scoped_session_key(session_key: str) -> tuple[UUID | None, str]:
    """Split ``agent_id:session_id`` into parts; return (None, key) if not scoped."""
    if ":" in session_key:
        agent_part, session_id = session_key.split(":", 1)
        if agent_part:
            try:
                return UUID(agent_part), session_id
            except ValueError:
                pass
    return None, session_key


class PostgresSessionStore(SqlSessionStore):
    """PostgreSQL-backed session store."""

    async def _upsert_session(
        self,
        db: AsyncSession,
        session_key: str,
        *,
        agent_id: UUID | None,
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

