from __future__ import annotations

from pydantic_ai.messages import ModelMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import ChatMessage
from deep_agent.db.serialization import deserialize_message, serialize_message
from deep_agent.db.stores._session import SessionBoundMixin

EARLY_MESSAGE_LIMIT = 5


class MessageStoreBase(SessionBoundMixin):
    async def get_history(self, session_key: str) -> list[ModelMessage]:
        async with self._read() as db:
            return await self._get_history(db, session_key)

    async def _get_history(self, db: AsyncSession, session_key: str) -> list[ModelMessage]:
        result = await db.execute(
            select(ChatMessage.payload)
            .where(ChatMessage.session_key == session_key)
            .order_by(ChatMessage.sequence)
        )
        return [deserialize_message(payload) for payload in result.scalars().all()]

    async def _get_max_sequence(self, db: AsyncSession, session_key: str) -> int:
        result = await db.execute(
            select(func.coalesce(func.max(ChatMessage.sequence), -1)).where(
                ChatMessage.session_key == session_key
            )
        )
        return result.scalar_one()

    async def _append_messages(
        self,
        db: AsyncSession,
        session_key: str,
        messages: list[ModelMessage],
        *,
        start_sequence: int,
    ) -> None:
        for offset, message in enumerate(messages):
            db.add(
                ChatMessage(
                    session_key=session_key,
                    sequence=start_sequence + offset,
                    payload=serialize_message(message),
                )
            )

    async def _fetch_early_payloads(
        self,
        db: AsyncSession,
        session_keys: list[str],
    ) -> dict[str, list[ModelMessage]]:
        if not session_keys:
            return {}

        result = await db.execute(
            select(ChatMessage.session_key, ChatMessage.payload)
            .where(
                ChatMessage.session_key.in_(session_keys),
                ChatMessage.sequence < EARLY_MESSAGE_LIMIT,
            )
            .order_by(ChatMessage.session_key, ChatMessage.sequence)
        )

        payloads_by_key: dict[str, list[ModelMessage]] = {}
        for session_key, payload in result.all():
            payloads_by_key.setdefault(session_key, []).append(deserialize_message(payload))
        return payloads_by_key
