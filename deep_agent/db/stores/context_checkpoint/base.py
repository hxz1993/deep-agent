from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import ChatContextCheckpoint
from deep_agent.db.session_scope import normalize_agent_scope
from deep_agent.db.stores._session import SessionBoundMixin


@dataclass(frozen=True)
class ContextCheckpointSnapshot:
    agent_id: str
    session_id: str
    source_last_sequence: int
    source_history_hash: str
    payload: list[dict[str, Any]]
    payload_schema_version: int
    projection_fingerprint: str
    estimated_tokens: int
    summary_model_id: str | None
    row_version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: ChatContextCheckpoint) -> "ContextCheckpointSnapshot":
        return cls(
            agent_id=row.agent_id,
            session_id=row.session_id,
            source_last_sequence=row.source_last_sequence,
            source_history_hash=row.source_history_hash,
            payload=list(row.payload),
            payload_schema_version=row.payload_schema_version,
            projection_fingerprint=row.projection_fingerprint,
            estimated_tokens=row.estimated_tokens,
            summary_model_id=row.summary_model_id,
            row_version=row.row_version,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class ContextCheckpointStoreBase(SessionBoundMixin):
    async def get(
        self,
        session_id: str,
        *,
        agent_id: str | None = None,
    ) -> ContextCheckpointSnapshot | None:
        scope = normalize_agent_scope(agent_id)
        async with self._read() as db:
            row = await self._get_row(db, scope, session_id)
            return ContextCheckpointSnapshot.from_row(row) if row is not None else None

    async def delete(
        self,
        session_id: str,
        *,
        agent_id: str | None = None,
    ) -> None:
        scope = normalize_agent_scope(agent_id)
        async with self._write() as db:
            await self._delete(db, scope, session_id)

    async def save_if_version(
        self,
        session_id: str,
        *,
        agent_id: str | None,
        expected_row_version: int,
        source_last_sequence: int,
        source_history_hash: str,
        payload: list[dict[str, Any]],
        payload_schema_version: int,
        projection_fingerprint: str,
        estimated_tokens: int,
        summary_model_id: str | None,
    ) -> bool:
        scope = normalize_agent_scope(agent_id)
        async with self._write() as db:
            row = await self._get_row(db, scope, session_id, for_update=True)
            if row is None:
                if expected_row_version != 0:
                    return False
                db.add(
                    ChatContextCheckpoint(
                        agent_id=scope,
                        session_id=session_id,
                        source_last_sequence=source_last_sequence,
                        source_history_hash=source_history_hash,
                        payload=payload,
                        payload_schema_version=payload_schema_version,
                        projection_fingerprint=projection_fingerprint,
                        estimated_tokens=estimated_tokens,
                        summary_model_id=summary_model_id,
                        row_version=1,
                    )
                )
                await db.flush()
                return True
            if row.row_version != expected_row_version:
                return False
            row.source_last_sequence = source_last_sequence
            row.source_history_hash = source_history_hash
            row.payload = payload
            row.payload_schema_version = payload_schema_version
            row.projection_fingerprint = projection_fingerprint
            row.estimated_tokens = estimated_tokens
            row.summary_model_id = summary_model_id
            row.row_version += 1
            await db.flush()
            return True

    async def _get_row(
        self,
        db: AsyncSession,
        agent_scope: str,
        session_id: str,
        *,
        for_update: bool = False,
    ) -> ChatContextCheckpoint | None:
        stmt = select(ChatContextCheckpoint).where(
            ChatContextCheckpoint.agent_id == agent_scope,
            ChatContextCheckpoint.session_id == session_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return (await db.execute(stmt)).scalar_one_or_none()

    async def _delete(
        self,
        db: AsyncSession,
        agent_scope: str,
        session_id: str,
    ) -> None:
        await db.execute(
            delete(ChatContextCheckpoint).where(
                ChatContextCheckpoint.agent_id == agent_scope,
                ChatContextCheckpoint.session_id == session_id,
            )
        )
