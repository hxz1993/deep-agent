from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import McpServer
from app.schemas.mcp import McpServerCreate, McpServerUpdate


class McpServerRepository:
    async def get_by_name(self, db: AsyncSession, name: str) -> McpServer | None:
        result = await db.execute(select(McpServer).where(McpServer.name == name))
        return result.scalar_one_or_none()

    async def list_all(self, db: AsyncSession) -> list[McpServer]:
        result = await db.execute(select(McpServer).order_by(McpServer.name))
        return list(result.scalars().all())

    async def create(self, db: AsyncSession, data: McpServerCreate) -> McpServer:
        row = McpServer(
            name=data.name,
            transport=data.transport,
            config=data.config,
            description=data.description,
            enabled=data.enabled,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row

    async def update(self, db: AsyncSession, row: McpServer, data: McpServerUpdate) -> McpServer:
        updates = data.model_dump(exclude_unset=True)
        for field, value in updates.items():
            setattr(row, field, value)
        await db.commit()
        await db.refresh(row)
        return row

    async def delete(self, db: AsyncSession, name: str) -> bool:
        result = await db.execute(delete(McpServer).where(McpServer.name == name))
        await db.commit()
        return result.rowcount > 0
