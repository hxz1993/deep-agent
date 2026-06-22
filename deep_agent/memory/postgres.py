from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from deep_agent.db.models import ChatSession
from deep_agent.memory.base import SessionStore
from deep_agent.memory.sql import SqlSessionStore


def scoped_session_key(agent_id: str, session_id: str) -> str:
    return f"{agent_id}:{session_id}"


def parse_scoped_session_key(session_key: str) -> tuple[str | None, str]:
    """Split ``agent_id:session_id`` into parts; return (None, key) if not scoped."""
    if ":" in session_key:
        agent_part, session_id = session_key.split(":", 1)
        if agent_part:
            return agent_part, session_id
    return None, session_key


async def resolve_session_key(
    store: SessionStore,
    session_ref: str,
    *,
    agent_id: str | None = None,
) -> str | None:
    """Resolve a display or storage session id to the store's session_key."""
    keys = await store.list_sessions()
    if session_ref in keys:
        return session_ref

    parsed_agent, display_id = parse_scoped_session_key(session_ref)
    search_id = display_id if parsed_agent else session_ref
    search_agent = parsed_agent or agent_id

    matches = [key for key in keys if parse_scoped_session_key(key)[1] == search_id]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    if not search_agent:
        return None

    for key in matches:
        key_agent, _ = parse_scoped_session_key(key)
        if key_agent == search_agent:
            return key
        if isinstance(store, SqlSessionStore):
            stored_agent_id = await store.get_session_agent_id(key)
            if stored_agent_id == search_agent:
                return key
    return None


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

