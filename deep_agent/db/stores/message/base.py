from __future__ import annotations

from pydantic_ai.messages import ModelMessage
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import ChatMessage
from deep_agent.db.serialization import deserialize_message, serialize_message
from deep_agent.db.stores._session import SessionBoundMixin

EARLY_MESSAGE_LIMIT = 5


class MessageStoreBase(SessionBoundMixin):
    async def get_history(self, agent_scope: str, session_id: str) -> list[ModelMessage]:
        async with self._read() as db:
            return await self._get_history(db, agent_scope, session_id)

    async def _get_history(self, db: AsyncSession, agent_scope: str, session_id: str) -> list[ModelMessage]:
        result = await db.execute(
            select(ChatMessage.payload)
            .where(
                ChatMessage.agent_id == agent_scope,
                ChatMessage.session_id == session_id,
            )
            .order_by(ChatMessage.sequence)
        )
        return [deserialize_message(payload) for payload in result.scalars().all()]

    async def _get_max_sequence(self, db: AsyncSession, agent_scope: str, session_id: str) -> int:
        result = await db.execute(
            select(func.coalesce(func.max(ChatMessage.sequence), -1)).where(
                ChatMessage.agent_id == agent_scope,
                ChatMessage.session_id == session_id,
            )
        )
        return result.scalar_one()

    async def _append_messages(
        self,
        db: AsyncSession,
        agent_scope: str,
        session_id: str,
        messages: list[ModelMessage],
        *,
        start_sequence: int,
    ) -> None:
        for offset, message in enumerate(messages):
            db.add(
                ChatMessage(
                    agent_id=agent_scope,
                    session_id=session_id,
                    sequence=start_sequence + offset,
                    payload=serialize_message(message),
                )
            )

    async def _fetch_early_payloads(
        self,
        db: AsyncSession,
        session_refs: list[tuple[str, str]],
    ) -> dict[tuple[str, str], list[ModelMessage]]:
        if not session_refs:
            return {}

        result = await db.execute(
            select(ChatMessage.agent_id, ChatMessage.session_id, ChatMessage.payload)
            .where(
                tuple_(ChatMessage.agent_id, ChatMessage.session_id).in_(session_refs),
                ChatMessage.sequence < EARLY_MESSAGE_LIMIT,
            )
            .order_by(ChatMessage.agent_id, ChatMessage.session_id, ChatMessage.sequence)
        )

        payloads_by_ref: dict[tuple[str, str], list[ModelMessage]] = {}
        for agent_scope, session_id, payload in result.all():
            payloads_by_ref.setdefault((agent_scope, session_id), []).append(deserialize_message(payload))
        return payloads_by_ref
