from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import Agent
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.schemas.agent import AgentCreate, AgentUpdate


class AgentStoreBase(SessionBoundMixin):
    async def get_by_id(self, agent_id: str) -> Agent | None:
        async with self._read() as db:
            return await self._get_by_id(db, agent_id)

    async def get_by_name(self, name: str) -> Agent | None:
        async with self._read() as db:
            return await self._get_by_name(db, name)

    async def list_all(self) -> list[Agent]:
        async with self._read() as db:
            return await self._list_all(db)

    async def create_unique(self, data: AgentCreate) -> Agent:
        async with self._write() as db:
            if await self._get_by_name(db, data.name) is not None:
                raise ValueError(f"Agent already exists: {data.name}")
            row = Agent(
                name=data.name,
                model_id=data.model_id,
                temperature=data.temperature,
                capabilities=data.capabilities,
                prompt=data.prompt,
            )
            db.add(row)
            await db.flush()
            await db.refresh(row)
            return row

    async def update_unique(self, agent_id: str, data: AgentUpdate) -> Agent | None:
        async with self._write() as db:
            row = await self._get_by_id(db, agent_id)
            if row is None:
                return None
            if data.name is not None and data.name != row.name:
                conflict = await self._get_by_name(db, data.name)
                if conflict is not None:
                    raise ValueError(f"Agent already exists: {data.name}")
            updates = data.model_dump(exclude_unset=True)
            for field, value in updates.items():
                setattr(row, field, value)
            await db.flush()
            await db.refresh(row)
            return row

    async def delete(self, agent_id: str) -> bool:
        async with self._write() as db:
            result = await db.execute(delete(Agent).where(Agent.id == agent_id))
            await db.flush()
            return result.rowcount > 0

    async def _get_by_id(self, db: AsyncSession, agent_id: str) -> Agent | None:
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        return result.scalar_one_or_none()

    async def _get_by_name(self, db: AsyncSession, name: str) -> Agent | None:
        result = await db.execute(select(Agent).where(Agent.name == name))
        return result.scalar_one_or_none()

    async def _list_all(self, db: AsyncSession) -> list[Agent]:
        result = await db.execute(select(Agent).order_by(Agent.name))
        return list(result.scalars().all())
