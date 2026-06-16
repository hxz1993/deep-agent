from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timezone
from uuid import UUID

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.models import ChatMessage, ChatSession

_message_adapter: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)


def _serialize_message(message: ModelMessage) -> dict:
    """JSON-compatible dict for DB persistence."""
    return _message_adapter.dump_python(message, mode="json")


class SqlSessionStore(ABC):
    """Reusable SQL-backed session store base class."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def lock(self, session_key: str) -> asyncio.Lock:
        return self._locks[session_key]

    @abstractmethod
    async def _upsert_session(
        self,
        db: AsyncSession,
        session_key: str,
        *,
        agent_id: UUID | None,
        user_id: str | None,
        now: datetime,
    ) -> None:
        """Dialect-specific upsert for ChatSession."""

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
            await self._upsert_session(
                db,
                session_key,
                agent_id=agent_id,
                user_id=user_id,
                now=now,
            )

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
