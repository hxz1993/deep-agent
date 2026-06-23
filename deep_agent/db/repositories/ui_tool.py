from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import UiToolDefinition
from deep_agent.schemas.ui_tools import UiToolCreate, UiToolUpdate


class UiToolRepository:
    async def get_by_name(self, db: AsyncSession, name: str) -> UiToolDefinition | None:
        result = await db.execute(select(UiToolDefinition).where(UiToolDefinition.name == name))
        return result.scalar_one_or_none()

    async def list_all(
        self,
        db: AsyncSession,
        *,
        source: str | None = None,
        mcp_server_name: str | None = None,
        enabled: bool | None = None,
    ) -> list[UiToolDefinition]:
        stmt = select(UiToolDefinition).order_by(UiToolDefinition.name)
        if source is not None:
            stmt = stmt.where(UiToolDefinition.source == source)
        if mcp_server_name is not None:
            stmt = stmt.where(UiToolDefinition.mcp_server_name == mcp_server_name)
        if enabled is not None:
            stmt = stmt.where(UiToolDefinition.enabled == enabled)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def create_manual(self, db: AsyncSession, data: UiToolCreate) -> UiToolDefinition:
        row = UiToolDefinition(
            name=data.name,
            description=data.description,
            parameters_json_schema=data.parameters_json_schema,
            widget=data.widget,
            result_schema=data.result_schema,
            metadata_=data.metadata,
            source="manual",
            mcp_server_name=None,
            enabled=data.enabled,
            override=data.override,
            agent_ids=data.agent_ids,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    async def update(
        self,
        db: AsyncSession,
        row: UiToolDefinition,
        data: UiToolUpdate,
    ) -> UiToolDefinition:
        updates = data.model_dump(exclude_unset=True)
        if "metadata" in updates:
            updates["metadata_"] = updates.pop("metadata")
        for field, value in updates.items():
            setattr(row, field, value)
        await db.flush()
        await db.refresh(row)
        return row

    async def delete(self, db: AsyncSession, name: str) -> bool:
        result = await db.execute(delete(UiToolDefinition).where(UiToolDefinition.name == name))
        await db.flush()
        return result.rowcount > 0

    async def delete_by_mcp_server(self, db: AsyncSession, mcp_server_name: str) -> int:
        result = await db.execute(
            delete(UiToolDefinition).where(
                UiToolDefinition.mcp_server_name == mcp_server_name,
                UiToolDefinition.source == "mcp",
            )
        )
        await db.flush()
        return result.rowcount

    async def upsert_mcp_tools(
        self,
        db: AsyncSession,
        mcp_server_name: str,
        tools: list[UiToolDefinition],
    ) -> tuple[int, int]:
        """Replace MCP-sourced tools for a server. Returns (upserted, removed)."""
        existing = await self.list_all(db, source="mcp", mcp_server_name=mcp_server_name)
        existing_names = {row.name for row in existing}
        new_names = {row.name for row in tools}

        removed = 0
        for row in existing:
            if row.name not in new_names:
                await db.delete(row)
                removed += 1

        upserted = 0
        for tool in tools:
            current = await self.get_by_name(db, tool.name)
            if current is not None:
                from deep_agent.capabilities.ui.constants import SOURCE_PRIORITY

                if SOURCE_PRIORITY[current.source] < SOURCE_PRIORITY["mcp"]:
                    continue
            if current is not None and current.source == "manual" and current.override:
                continue
            if current is None:
                db.add(tool)
                upserted += 1
            else:
                current.description = tool.description
                current.parameters_json_schema = tool.parameters_json_schema
                current.widget = tool.widget
                current.result_schema = tool.result_schema
                current.metadata_ = tool.metadata_
                current.source = "mcp"
                current.mcp_server_name = mcp_server_name
                current.discovered_at = tool.discovered_at
                if not current.override:
                    current.enabled = tool.enabled
                upserted += 1

        await db.flush()
        return upserted, removed
