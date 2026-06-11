from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.spec import AgentSpec
from app.db.models import Agent
from app.schemas.agent import AgentCreate, AgentUpdate


class AgentRepository:
    @staticmethod
    def to_spec(row: Agent) -> AgentSpec:
        return AgentSpec(
            name=row.name,
            model=row.model,
            instructions=row.prompt,
            capabilities=row.capabilities,
            base_url=row.base_url,
            api_key=row.api_key,
        )

    async def get_by_id(self, db: AsyncSession, agent_id: UUID) -> Agent | None:
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        return result.scalar_one_or_none()

    async def get_by_name(self, db: AsyncSession, name: str) -> Agent | None:
        result = await db.execute(select(Agent).where(Agent.name == name))
        return result.scalar_one_or_none()

    async def list_all(self, db: AsyncSession) -> list[Agent]:
        result = await db.execute(select(Agent).order_by(Agent.name))
        return list(result.scalars().all())

    async def create(self, db: AsyncSession, data: AgentCreate) -> Agent:
        row = Agent(
            name=data.name,
            model=data.model,
            base_url=data.base_url,
            api_key=data.api_key,
            capabilities=data.capabilities,
            prompt=data.prompt,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row

    async def update(self, db: AsyncSession, row: Agent, data: AgentUpdate) -> Agent:
        updates = data.model_dump(exclude_unset=True)
        for field, value in updates.items():
            setattr(row, field, value)
        await db.commit()
        await db.refresh(row)
        return row

    async def delete(self, db: AsyncSession, agent_id: UUID) -> bool:
        result = await db.execute(delete(Agent).where(Agent.id == agent_id))
        await db.commit()
        return result.rowcount > 0
