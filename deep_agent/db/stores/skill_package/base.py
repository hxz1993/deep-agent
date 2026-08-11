from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from deep_agent.db.models import SkillPackage
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.schemas.skill import SkillPackageRecord, SkillUpdate


class SkillPackageStoreBase(SessionBoundMixin):
    async def get_by_code(self, code: str) -> SkillPackage | None:
        async with self._read() as db:
            result = await db.execute(select(SkillPackage).where(SkillPackage.code == code))
            return result.scalar_one_or_none()

    async def list_all(self, *, enabled: bool | None = None) -> list[SkillPackage]:
        async with self._read() as db:
            stmt = select(SkillPackage).order_by(SkillPackage.label, SkillPackage.code)
            if enabled is not None:
                stmt = stmt.where(SkillPackage.enabled == enabled)
            result = await db.execute(stmt)
            return list(result.scalars().all())

    async def create(self, data: SkillPackageRecord) -> SkillPackage:
        try:
            async with self._write() as db:
                row = SkillPackage(**data.model_dump())
                db.add(row)
                await db.flush()
                await db.refresh(row)
                return row
        except IntegrityError as exc:
            raise ValueError(f"Skill already exists: {data.code}") from exc

    async def replace_package(
        self,
        code: str,
        data: SkillPackageRecord,
    ) -> SkillPackage | None:
        async with self._write() as db:
            result = await db.execute(select(SkillPackage).where(SkillPackage.code == code))
            row = result.scalar_one_or_none()
            if row is None:
                return None
            for field, value in data.model_dump(exclude={"code", "source"}).items():
                setattr(row, field, value)
            await db.flush()
            await db.refresh(row)
            return row

    async def update(self, code: str, data: SkillUpdate) -> SkillPackage | None:
        async with self._write() as db:
            result = await db.execute(select(SkillPackage).where(SkillPackage.code == code))
            row = result.scalar_one_or_none()
            if row is None:
                return None
            for field, value in data.model_dump(exclude_unset=True).items():
                setattr(row, field, value)
            await db.flush()
            await db.refresh(row)
            return row

    async def update_manifest(self, code: str, manifest: dict) -> SkillPackage | None:
        async with self._write() as db:
            result = await db.execute(select(SkillPackage).where(SkillPackage.code == code))
            row = result.scalar_one_or_none()
            if row is None:
                return None
            row.manifest = manifest
            await db.flush()
            await db.refresh(row)
            return row

    async def delete(self, code: str) -> bool:
        async with self._write() as db:
            result = await db.execute(delete(SkillPackage).where(SkillPackage.code == code))
            await db.flush()
            return result.rowcount > 0
