from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import Agent, LlmModel
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.schemas.llm_model import LlmModelCreate, LlmModelUpdate


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

    async def create_unique(self, data: LlmModelCreate) -> LlmModel:
        async with self._write() as db:
            if await self._get_by_name(db, data.name) is not None:
                raise ValueError(f"LLM model already exists: {data.name}")
            row = LlmModel(
                name=data.name,
                description=data.description,
                model=data.model,
                base_url=data.base_url,
                api_key=data.api_key,
                input_modalities=data.input_modalities or None,
                capabilities_source="user" if data.input_modalities else None,
            )
            db.add(row)
            await db.flush()
            await db.refresh(row)
            return row

    async def update_unique(self, model_id: str, data: LlmModelUpdate) -> LlmModel | None:
        async with self._write() as db:
            row = await self._get_by_id(db, model_id)
            if row is None:
                return None
            if data.name is not None and data.name != row.name:
                conflict = await self._get_by_name(db, data.name)
                if conflict is not None:
                    raise ValueError(f"LLM model already exists: {data.name}")
            updates = data.model_dump(exclude_unset=True)
            modalities_mode = updates.pop("modalities_mode", None)
            if modalities_mode == "auto":
                row.input_modalities = None
                row.capabilities_source = None
            elif modalities_mode == "manual" and "input_modalities" in updates:
                row.input_modalities = updates["input_modalities"] or None
                row.capabilities_source = "user"
            elif "input_modalities" in updates:
                row.capabilities_source = "user"
            for field, value in updates.items():
                setattr(row, field, value)
            await db.flush()
            await db.refresh(row)
            return row

    async def delete(self, model_id: str) -> bool:
        async with self._write() as db:
            result = await db.execute(delete(LlmModel).where(LlmModel.id == model_id))
            await db.flush()
            return result.rowcount > 0

    async def update_metadata(
        self,
        model_id: str,
        *,
        input_modalities: list[str],
        capabilities_source: str,
    ) -> LlmModel | None:
        async with self._write() as db:
            row = await self._get_by_id(db, model_id)
            if row is None:
                return None
            row.input_modalities = input_modalities if input_modalities else None
            row.capabilities_source = capabilities_source
            await db.flush()
            await db.refresh(row)
            return row

    async def _get_by_id(self, db: AsyncSession, model_id: str) -> LlmModel | None:
        result = await db.execute(select(LlmModel).where(LlmModel.id == model_id))
        return result.scalar_one_or_none()

    async def _get_by_name(self, db: AsyncSession, name: str) -> LlmModel | None:
        result = await db.execute(select(LlmModel).where(LlmModel.name == name))
        return result.scalar_one_or_none()

    async def _list_all(self, db: AsyncSession) -> list[LlmModel]:
        result = await db.execute(select(LlmModel).order_by(LlmModel.name))
        return list(result.scalars().all())
