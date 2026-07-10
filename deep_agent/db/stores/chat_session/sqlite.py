from __future__ import annotations

from datetime import datetime

from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import ChatSession
from deep_agent.db.stores.chat_session.base import ChatSessionStoreBase


class SqliteChatSessionStore(ChatSessionStoreBase):
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
        stmt = stmt.on_conflict_do_update(
            index_elements=[ChatSession.agent_id, ChatSession.session_id],
            set_={
                "updated_at": now,
                **({"user_id": user_id} if user_id is not None else {}),
                **({"title": title} if title is not None else {}),
            },
        )
        await db.execute(stmt)
