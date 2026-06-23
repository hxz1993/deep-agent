from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import McpServer
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.schemas.mcp import McpServerCreate, McpServerUpdate


class McpServerStoreBase(SessionBoundMixin):
    async def get_by_name(self, name: str) -> McpServer | None:
        async with self._read() as db:
            return await self._get_by_name(db, name)

    async def list_all(self) -> list[McpServer]:
        async with self._read() as db:
            return await self._list_all(db)

    async def create_unique(self, data: McpServerCreate) -> McpServer:
        async with self._write() as db:
            if await self._get_by_name(db, data.name) is not None:
                raise ValueError(f"MCP server already exists: {data.name}")
            row = McpServer(
                name=data.name,
                transport=data.transport,
                config=data.config,
                description=data.description,
                enabled=data.enabled,
            )
            db.add(row)
            await db.flush()
            await db.refresh(row)
            return row

    async def update(self, name: str, data: McpServerUpdate) -> McpServer | None:
        async with self._write() as db:
            row = await self._get_by_name(db, name)
            if row is None:
                return None
            updates = data.model_dump(exclude_unset=True)
            for field, value in updates.items():
                setattr(row, field, value)
            await db.flush()
            await db.refresh(row)
            return row

    async def delete(self, name: str) -> bool:
        async with self._write() as db:
            result = await db.execute(delete(McpServer).where(McpServer.name == name))
            await db.flush()
            return result.rowcount > 0

    async def _get_by_name(self, db: AsyncSession, name: str) -> McpServer | None:
        result = await db.execute(select(McpServer).where(McpServer.name == name))
        return result.scalar_one_or_none()

    async def _list_all(self, db: AsyncSession) -> list[McpServer]:
        result = await db.execute(select(McpServer).order_by(McpServer.name))
        return list(result.scalars().all())
