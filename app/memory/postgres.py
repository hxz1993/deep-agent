from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from uuid import UUID

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ChatMessage, ChatSession

_message_adapter: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)


def _serialize_message(message: ModelMessage) -> dict:
    """JSON-compatible dict for JSONB (converts datetime, bytes, etc.)."""
    return _message_adapter.dump_python(message, mode="json")


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


class PostgresSessionStore:
    """PostgreSQL-backed session store implementing the SessionStore protocol."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def lock(self, session_key: str) -> asyncio.Lock:
        return self._locks[session_key]

    async def get_history(self, session_key: str) -> list[ModelMessage]:
        async with self._session_factory() as db:
            result = await db.execute(
                select(ChatMessage.payload)
                .where(ChatMessage.session_key == session_key)
                .order_by(ChatMessage.sequence)
            )
            payloads = result.scalars().all()
        return [_message_adapter.validate_python(payload) for payload in payloads]

    async def append(
        self,
        session_key: str,
        messages: list[ModelMessage],
        *,
        agent_id: UUID | None = None,
        user_id: str | None = None,
    ) -> None:
        if not messages:
            return

        async with self._session_factory() as db:
            now = datetime.now(timezone.utc)
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

            result = await db.execute(
                select(func.coalesce(func.max(ChatMessage.sequence), -1)).where(
                    ChatMessage.session_key == session_key
                )
            )
            next_sequence = result.scalar_one() + 1

            for offset, message in enumerate(messages):
                db.add(
                    ChatMessage(
                        session_key=session_key,
                        sequence=next_sequence + offset,
                        payload=_serialize_message(message),
                    )
                )

            await db.commit()

    async def list_sessions(self) -> list[str]:
        async with self._session_factory() as db:
            result = await db.execute(
                select(ChatSession.session_key).order_by(ChatSession.updated_at.desc())
            )
            return list(result.scalars().all())

    async def get_session_agent_id(self, session_key: str) -> UUID | None:
        async with self._session_factory() as db:
            result = await db.execute(
                select(ChatSession.agent_id).where(ChatSession.session_key == session_key)
            )
            return result.scalar_one_or_none()

    async def delete(self, session_key: str) -> None:
        async with self._session_factory() as db:
            await db.execute(delete(ChatSession).where(ChatSession.session_key == session_key))
            await db.commit()
        self._locks.pop(session_key, None)
