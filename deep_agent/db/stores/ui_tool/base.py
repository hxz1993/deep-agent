from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import UiToolDefinition
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.schemas.ui_tools import UiToolCreate, UiToolUpdate


class UiToolStoreBase(SessionBoundMixin):
    async def get_by_name(self, name: str) -> UiToolDefinition | None:
        async with self._read() as db:
            return await self._get_by_name(db, name)

    async def list_all(
        self,
        *,
        source: str | None = None,
        enabled: bool | None = None,
    ) -> list[UiToolDefinition]:
        async with self._read() as db:
            return await self._list_all(
                db,
                source=source,
                enabled=enabled,
            )

    async def create_manual(self, data: UiToolCreate) -> UiToolDefinition:
        try:
            async with self._write() as db:
                row = UiToolDefinition(
                    name=data.name,
                    description=data.description,
                    parameters_json_schema=data.parameters_json_schema,
                    widget=data.widget,
                    result_schema=data.result_schema,
                    metadata_=data.metadata,
                    source="manual",
                    enabled=data.enabled,
                )
                db.add(row)
                await db.flush()
                await db.refresh(row)
                return row
        except IntegrityError as exc:
            raise ValueError(f"UI tool already exists: {data.name}") from exc

    async def update(self, name: str, data: UiToolUpdate) -> UiToolDefinition | None:
        async with self._write() as db:
            row = await self._get_by_name(db, name)
            if row is None:
                return None
            updates = data.model_dump(exclude_unset=True)
            if "metadata" in updates:
                updates["metadata_"] = updates.pop("metadata")
            for field, value in updates.items():
                setattr(row, field, value)
            await db.flush()
            await db.refresh(row)
            return row

    async def delete(self, name: str) -> bool:
        async with self._write() as db:
            result = await db.execute(delete(UiToolDefinition).where(UiToolDefinition.name == name))
            await db.flush()
            return result.rowcount > 0

    async def _get_by_name(self, db: AsyncSession, name: str) -> UiToolDefinition | None:
        result = await db.execute(select(UiToolDefinition).where(UiToolDefinition.name == name))
        return result.scalar_one_or_none()

    async def _get_by_names(self, db: AsyncSession, names: list[str]) -> dict[str, UiToolDefinition]:
        if not names:
            return {}
        result = await db.execute(select(UiToolDefinition).where(UiToolDefinition.name.in_(names)))
        return {row.name: row for row in result.scalars().all()}

    async def _list_all(
        self,
        db: AsyncSession,
        *,
        source: str | None = None,
        enabled: bool | None = None,
    ) -> list[UiToolDefinition]:
        stmt = select(UiToolDefinition).order_by(UiToolDefinition.name)
        if source is not None:
            stmt = stmt.where(UiToolDefinition.source == source)
        if enabled is not None:
            stmt = stmt.where(UiToolDefinition.enabled == enabled)
        result = await db.execute(stmt)
        return list(result.scalars().all())
