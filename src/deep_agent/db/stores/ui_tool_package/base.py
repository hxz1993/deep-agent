from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from deep_agent.db.models import UiToolDefinition, UiToolPackage, Agent
from deep_agent.db.reference_integrity import ReferenceConflict, lock_optional, is_unique_conflict
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.schemas.ui_tools import UiToolPackageCreate, UiToolPackageUpdate


class UiToolPackageStoreBase(SessionBoundMixin):
    async def get_by_code(self, code: str) -> UiToolPackage | None:
        async with self._read() as db:
            result = await db.execute(select(UiToolPackage).where(UiToolPackage.code == code))
            return result.scalar_one_or_none()

    async def list_all(self) -> list[UiToolPackage]:
        async with self._read() as db:
            stmt = select(UiToolPackage).order_by(UiToolPackage.code)
            result = await db.execute(stmt)
            return list(result.scalars().all())

    async def list_tool_names(self, code: str) -> list[str]:
        async with self._read() as db:
            result = await db.execute(
                select(UiToolDefinition.name)
                .where(UiToolDefinition.package_code == code)
                .order_by(UiToolDefinition.name)
            )
            return list(result.scalars().all())

    async def tool_counts(self) -> dict[str, int]:
        async with self._read() as db:
            result = await db.execute(
                select(UiToolDefinition.package_code, func.count(UiToolDefinition.name)).group_by(
                    UiToolDefinition.package_code
                )
            )
            return {str(code): int(count) for code, count in result.all()}

    async def create_manual(self, data: UiToolPackageCreate) -> UiToolPackage:
        try:
            async with self._write() as db:
                row = UiToolPackage(
                    code=data.code,
                    label=data.label,
                    description=data.description,
                    source="manual",
                )
                db.add(row)
                await db.flush()
                await db.refresh(row)
                return row
        except IntegrityError as exc:
            if not is_unique_conflict(exc):
                raise
            raise ValueError(f"UI tool package already exists: {data.code}") from exc

    async def update(
        self, code: str, data: UiToolPackageUpdate
    ) -> UiToolPackage | None:
        async with self._write() as db:
            result = await db.execute(select(UiToolPackage).where(UiToolPackage.code == code))
            row = result.scalar_one_or_none()
            if row is None:
                return None
            for field, value in data.model_dump(exclude_unset=True).items():
                setattr(row, field, value)
            await db.flush()
            await db.refresh(row)
            return row

    async def delete(self, code: str) -> bool:
        async with self._write() as db:
            if await lock_optional(db, UiToolPackage, UiToolPackage.code, code) is None:
                return False
            tools = (await db.execute(select(UiToolDefinition.name).where(
                UiToolDefinition.package_code == code).order_by(UiToolDefinition.name).with_for_update())).all()
            refs = (await db.execute(select(Agent.name, Agent.capabilities).order_by(Agent.id).with_for_update())).all()
            if tools or any(code in (caps or []) for _, caps in refs):
                raise ReferenceConflict("UI 工具包包含工具或正在被 Agent 引用，不能删除")
            result = await db.execute(delete(UiToolPackage).where(UiToolPackage.code == code))
            await db.flush()
            return result.rowcount > 0
