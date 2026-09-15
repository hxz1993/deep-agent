from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import UiToolDefinition, UiToolPackage, Agent
from deep_agent.db.reference_integrity import ReferenceConflict, lock_reference, lock_optional, is_unique_conflict
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
    ) -> list[UiToolDefinition]:
        async with self._read() as db:
            return await self._list_all(
                db,
                source=source,
            )

    async def create_manual(self, data: UiToolCreate) -> UiToolDefinition:
        try:
            async with self._write() as db:
                await lock_reference(db, UiToolPackage, UiToolPackage.code, data.package_code, "UI tool package")
                row = UiToolDefinition(
                    name=data.name,
                    package_code=data.package_code,
                    description=data.description,
                    parameters_json_schema=data.parameters_json_schema,
                    widget=data.widget,
                    result_schema=data.result_schema,
                    metadata_=data.metadata,
                    source="manual",
                )
                db.add(row)
                await db.flush()
                await db.refresh(row)
                return row
        except IntegrityError as exc:
            if not is_unique_conflict(exc):
                raise
            raise ValueError(f"UI tool already exists: {data.name}") from exc

    async def update(self, name: str, data: UiToolUpdate) -> UiToolDefinition | None:
        async with self._write() as db:
            row = await self._get_by_name(db, name)
            if row is None:
                return None
            previous_package = row.package_code
            for code in sorted({previous_package, data.package_code or previous_package}):
                await lock_reference(db, UiToolPackage, UiToolPackage.code, code, "UI tool package")
            row = await lock_optional(db, UiToolDefinition, UiToolDefinition.name, name)
            if row is None:
                return None
            if row.package_code != previous_package:
                raise ReferenceConflict("UI tool package changed concurrently; refresh and retry")
            if data.package_code is not None and data.package_code != previous_package:
                refs = (await db.execute(
                    select(Agent.ui_tool_selection).order_by(Agent.id).with_for_update()
                )).scalars().all()
                if any(name in (selection or {}).get(previous_package, []) for selection in refs):
                    raise ReferenceConflict("UI 工具已被 Agent 单独选择，请先取消选择后再移动工具包")
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
            current = await self._get_by_name(db, name)
            if current is None:
                return False
            code = current.package_code
            await lock_reference(db, UiToolPackage, UiToolPackage.code, code, "UI tool package")
            row = await lock_optional(db, UiToolDefinition, UiToolDefinition.name, name)
            if row is None:
                return False
            if row.package_code != code:
                raise ReferenceConflict("UI tool package changed concurrently; refresh and retry")
            refs = (await db.execute(select(Agent.capabilities, Agent.ui_tool_selection).order_by(Agent.id).with_for_update())).all()
            if any(
                code in (caps or []) and (code not in (selection or {}) or name in selection[code])
                for caps, selection in refs
            ):
                raise ReferenceConflict("UI 工具正在被 Agent 引用，不能删除")
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
    ) -> list[UiToolDefinition]:
        stmt = select(UiToolDefinition).order_by(UiToolDefinition.name)
        if source is not None:
            stmt = stmt.where(UiToolDefinition.source == source)
        result = await db.execute(stmt)
        return list(result.scalars().all())
