from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import case, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import Agent, McpServer
from deep_agent.db.reference_integrity import ReferenceConflict, lock_optional
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.schemas.mcp import McpServerCreate, McpServerUpdate

_INSPECTION_UNSET = object()


@dataclass(slots=True)
class McpRenameStoreResult:
    row: McpServer
    updated_agent_names: list[str]


class McpServerStoreBase(SessionBoundMixin):
    async def get_by_name(self, name: str) -> McpServer | None:
        async with self._read() as db:
            return await self._get_by_name(db, name)

    async def list_all(self) -> list[McpServer]:
        async with self._read() as db:
            return await self._list_all(db)

    async def list_for_admin(self) -> list[McpServer]:
        """List user-managed MCPs first without changing runtime tool ordering."""
        async with self._read() as db:
            result = await db.execute(
                select(McpServer).order_by(
                    case((McpServer.transport == "builtin", 1), else_=0),
                    McpServer.created_at.desc(),
                    McpServer.name,
                )
            )
            return list(result.scalars().all())

    async def create_unique(
        self,
        data: McpServerCreate,
        *,
        inspection: dict | None = None,
    ) -> McpServer:
        async with self._write() as db:
            if await self._get_by_name(db, data.name) is not None:
                raise ValueError(f"MCP server already exists: {data.name}")
            row = McpServer(
                name=data.name,
                transport=data.transport,
                config=data.config,
                description=data.description,
                inspection=inspection,
            )
            db.add(row)
            try:
                await db.flush()
            except IntegrityError as exc:
                raise ValueError(f"MCP server already exists: {data.name}") from exc
            await db.refresh(row)
            return row

    async def update(
        self,
        name: str,
        data: McpServerUpdate,
        *,
        inspection: dict | None | object = _INSPECTION_UNSET,
    ) -> McpServer | None:
        async with self._write() as db:
            row = await self._get_by_name(db, name)
            if row is None:
                return None
            updates = data.model_dump(exclude_unset=True)
            for field, value in updates.items():
                setattr(row, field, value)
            if inspection is not _INSPECTION_UNSET:
                row.inspection = inspection
            await db.flush()
            await db.refresh(row)
            return row

    async def update_inspection(
        self,
        name: str,
        inspection: dict,
    ) -> McpServer | None:
        async with self._write() as db:
            row = await self._get_by_name(db, name)
            if row is None:
                return None
            row.inspection = inspection
            await db.flush()
            await db.refresh(row)
            return row

    async def rename_with_agent_references(
        self,
        old_name: str,
        new_name: str,
    ) -> McpRenameStoreResult | None:
        """Rename a URL MCP and migrate Agent package references atomically."""
        try:
            async with self._write() as db:
                source_result = await db.execute(
                    select(McpServer)
                    .where(McpServer.name == old_name)
                    .with_for_update()
                )
                row = source_result.scalar_one_or_none()
                if row is None:
                    return None
                if row.transport == "builtin":
                    raise PermissionError("内建 MCP 由程序管理，不能重命名")
                if row.transport == "stdio":
                    raise PermissionError("stdio MCP 已停止支持；历史配置不能重命名")
                if new_name == old_name:
                    return McpRenameStoreResult(row=row, updated_agent_names=[])

                conflict = await self._get_by_name(db, new_name)
                if conflict is not None:
                    raise ValueError(f"MCP server already exists: {new_name}")

                old_capability = f"mcp:{old_name}"
                new_capability = f"mcp:{new_name}"
                agent_result = await db.execute(
                    select(Agent).order_by(Agent.id).with_for_update()
                )
                updated_agent_names: list[str] = []
                for agent in agent_result.scalars().all():
                    capabilities = agent.capabilities
                    if old_capability not in capabilities:
                        continue
                    agent.capabilities = [
                        new_capability if capability == old_capability else capability
                        for capability in capabilities
                    ]
                    updated_agent_names.append(agent.name)

                row.name = new_name
                await db.flush()
                await db.refresh(row)
                return McpRenameStoreResult(
                    row=row,
                    updated_agent_names=sorted(updated_agent_names),
                )
        except IntegrityError as exc:
            raise ValueError(f"MCP server already exists: {new_name}") from exc

    async def delete(self, name: str) -> bool:
        async with self._write() as db:
            if await lock_optional(db, McpServer, McpServer.name, name) is None:
                return False
            refs = (await db.execute(
                select(Agent.capabilities).order_by(Agent.id).with_for_update()
            )).scalars().all()
            if any(f"mcp:{name}" in (caps or []) for caps in refs):
                raise ReferenceConflict("MCP 正在被 Agent 引用，不能删除")
            result = await db.execute(delete(McpServer).where(McpServer.name == name))
            await db.flush()
            return result.rowcount > 0

    async def _get_by_name(self, db: AsyncSession, name: str) -> McpServer | None:
        result = await db.execute(select(McpServer).where(McpServer.name == name))
        return result.scalar_one_or_none()

    async def _list_all(self, db: AsyncSession) -> list[McpServer]:
        result = await db.execute(select(McpServer).order_by(McpServer.name))
        return list(result.scalars().all())
