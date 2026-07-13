from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.agent.auxiliary_models import AUXILIARY_MODEL_CATALOG
from deep_agent.db.models import AuxiliaryModelConfig
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.schemas.auxiliary_model import AuxiliaryModelUpdate


class AuxiliaryModelStoreBase(SessionBoundMixin):
    async def get_by_use_case(self, use_case: str) -> AuxiliaryModelConfig | None:
        async with self._read() as db:
            return await self._get_by_use_case(db, use_case)

    async def list_all(self) -> list[AuxiliaryModelConfig]:
        async with self._read() as db:
            return await self._list_all(db)

    async def ensure_defaults(self) -> None:
        async with self._write() as db:
            for use_case in AUXILIARY_MODEL_CATALOG:
                if await self._get_by_use_case(db, use_case) is None:
                    db.add(AuxiliaryModelConfig(use_case=use_case, llm_model_id=None))
            await db.flush()

    async def update(self, use_case: str, data: AuxiliaryModelUpdate) -> AuxiliaryModelConfig | None:
        async with self._write() as db:
            row = await self._get_by_use_case(db, use_case)
            if row is None:
                return None
            if "llm_model_id" in data.model_fields_set:
                row.llm_model_id = data.llm_model_id
            await db.flush()
            await db.refresh(row)
            return row

    async def _get_by_use_case(self, db: AsyncSession, use_case: str) -> AuxiliaryModelConfig | None:
        result = await db.execute(
            select(AuxiliaryModelConfig).where(AuxiliaryModelConfig.use_case == use_case)
        )
        return result.scalar_one_or_none()

    async def _list_all(self, db: AsyncSession) -> list[AuxiliaryModelConfig]:
        result = await db.execute(
            select(AuxiliaryModelConfig).order_by(AuxiliaryModelConfig.use_case)
        )
        return list(result.scalars().all())
