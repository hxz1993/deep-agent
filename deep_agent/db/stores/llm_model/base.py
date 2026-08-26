from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import Agent, LlmModel
from deep_agent.db.stores._session import SessionBoundMixin
class LlmModelValidationError(ValueError):
    pass


def _validate_runtime_metadata(row: LlmModel) -> None:
    if row.input_modalities is None or not row.input_modalities:
        raise LlmModelValidationError("input_modalities must contain at least one value")
    if row.context_window_tokens is None:
        raise LlmModelValidationError("context_window_tokens is required")
    if row.max_output_tokens is None:
        raise LlmModelValidationError("max_output_tokens is required")
    if row.max_output_tokens > row.context_window_tokens:
        raise LlmModelValidationError(
            "max_output_tokens must not exceed context_window_tokens"
        )


class LlmModelStoreBase(SessionBoundMixin):
    async def get_by_id(self, model_id: str) -> LlmModel | None:
        async with self._read() as db:
            return await self._get_by_id(db, model_id)

    async def get_by_name(self, name: str) -> LlmModel | None:
        async with self._read() as db:
            return await self._get_by_name(db, name)

    async def list_all(self) -> list[LlmModel]:
        async with self._read() as db:
            return await self._list_all(db)

    async def count_agents(self, model_id: str) -> int:
        async with self._read() as db:
            result = await db.execute(
                select(func.count()).select_from(Agent).where(Agent.model_id == model_id)
            )
            return int(result.scalar_one())

    async def count_agents_by_model(self) -> dict[str, int]:
        async with self._read() as db:
            result = await db.execute(
                select(Agent.model_id, func.count()).group_by(Agent.model_id)
            )
            return {str(model_id): int(count) for model_id, count in result.all()}

    async def count_references(self, model_id: str) -> int:
        async with self._read() as db:
            agents = await db.execute(
                select(func.count()).select_from(Agent).where(Agent.model_id == model_id)
            )
            return int(agents.scalar_one())

    async def _get_by_id(self, db: AsyncSession, model_id: str) -> LlmModel | None:
        result = await db.execute(select(LlmModel).where(LlmModel.id == model_id))
        return result.scalar_one_or_none()

    async def _get_by_name(self, db: AsyncSession, name: str) -> LlmModel | None:
        result = await db.execute(
            select(LlmModel).where(LlmModel.name == name).order_by(LlmModel.id)
        )
        return result.scalars().first()

    async def _list_all(self, db: AsyncSession) -> list[LlmModel]:
        result = await db.execute(select(LlmModel).order_by(LlmModel.name))
        return list(result.scalars().all())
