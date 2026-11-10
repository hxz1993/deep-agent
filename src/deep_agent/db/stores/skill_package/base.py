from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from deep_agent.db.models import Agent, SkillPackage
from deep_agent.db.reference_integrity import ReferenceConflict, lock_optional
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.db.stores.integration_app import app_skill_rows, ensure_app_scope, other_app_skill_references
from deep_agent.integrations.app_scope import (
    GENERAL_SKILL_SCOPE, app_skill_scope, app_skill_storage_key, skill_package_code, skill_scope_condition,
)
from deep_agent.schemas.skill import SkillPackageRecord, SkillUpdate


class SkillPackageStoreBase(SessionBoundMixin):
    async def get_by_code(self, code: str, *, scope_ref: str = GENERAL_SKILL_SCOPE) -> SkillPackage | None:
        async with self._read() as db:
            result = await db.execute(select(SkillPackage).where(
                SkillPackage.code == code, skill_scope_condition(SkillPackage.scope_ref, scope_ref)
            ))
            return result.scalar_one_or_none()

    async def list_all(self, *, scope_ref: str = GENERAL_SKILL_SCOPE) -> list[SkillPackage]:
        async with self._read() as db:
            stmt = select(SkillPackage).where(skill_scope_condition(SkillPackage.scope_ref, scope_ref)).order_by(SkillPackage.label, SkillPackage.code)
            result = await db.execute(stmt)
            return list(result.scalars().all())

    async def list_app_packages(self, scope_ref: str, names: tuple[str, ...]) -> list[SkillPackage]:
        if not names:
            return []
        wanted = set(names)
        async with self._read() as db:
            return [row for row in await app_skill_rows(db, scope_ref) if skill_package_code(row) in wanted]

    async def create(self, data: SkillPackageRecord, *, app=None, expected_scope_id: str | None = None) -> SkillPackage:
        try:
            async with self._write(exclusive=app is not None) as db:
                values = data.model_dump()
                if app is not None:
                    scope = await ensure_app_scope(db, app=app, expected_id=expected_scope_id)
                    scope_ref = app_skill_scope(app.app_id)
                    if any(skill_package_code(row) == data.code for row in await app_skill_rows(db, scope_ref)):
                        raise ValueError(f"Skill already exists: {data.code}")
                    values["scope_ref"] = scope_ref
                    values["code"] = app_skill_storage_key(app.app_id, data.code)
                    values["manifest"] = {**values["manifest"], "code": data.code}
                    scope.settings = {**scope.settings, "skills": [*scope.settings["skills"], data.code]}
                else:
                    values["scope_ref"] = GENERAL_SKILL_SCOPE
                row = SkillPackage(**values)
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
        *,
        scope_ref: str = GENERAL_SKILL_SCOPE,
        expected_file_id: str | None = None,
        app=None,
        expected_scope_id: str | None = None,
    ) -> SkillPackage | None:
        async with self._write(exclusive=app is not None) as db:
            config = await ensure_app_scope(db, app=app, expected_id=expected_scope_id) if app is not None else None
            result = await db.execute(select(SkillPackage).where(
                SkillPackage.code == code, skill_scope_condition(SkillPackage.scope_ref, scope_ref)
            ).with_for_update())
            row = result.scalar_one_or_none()
            if row is None or (config is not None and skill_package_code(row) not in config.settings["skills"]):
                return None
            if expected_file_id is not None and row.platform_file_id != expected_file_id:
                raise ReferenceConflict("Skill changed during replacement; refresh and retry")
            values = data.model_dump(exclude={"code", "source", "scope_ref"})
            if scope_ref != GENERAL_SKILL_SCOPE:
                values["manifest"] = {**values["manifest"], "code": data.code}
            for field, value in values.items():
                setattr(row, field, value)
            await db.flush()
            await db.refresh(row)
            return row

    async def update(
        self, code: str, data: SkillUpdate, *, scope_ref: str = GENERAL_SKILL_SCOPE,
        app=None, expected_scope_id: str | None = None,
    ) -> SkillPackage | None:
        async with self._write(exclusive=app is not None) as db:
            config = await ensure_app_scope(db, app=app, expected_id=expected_scope_id) if app is not None else None
            result = await db.execute(select(SkillPackage).where(
                SkillPackage.code == code, skill_scope_condition(SkillPackage.scope_ref, scope_ref)
            ))
            row = result.scalar_one_or_none()
            if row is None or (config is not None and skill_package_code(row) not in config.settings["skills"]):
                return None
            for field, value in data.model_dump(exclude_unset=True).items():
                setattr(row, field, value)
            await db.flush()
            await db.refresh(row)
            return row

    async def delete(
        self, code: str, *, scope_ref: str = GENERAL_SKILL_SCOPE, expected_file_id: str | None = None,
        app=None, expected_scope_id: str | None = None,
    ) -> bool:
        # Exclude installation/configuration writers until reference validation
        # and deletion commit together. The API preflight can already be stale.
        async with self._write(exclusive=True) as db:
            config = await ensure_app_scope(db, app=app, expected_id=expected_scope_id) if app is not None else None
            row = await lock_optional(db, SkillPackage, SkillPackage.code, code)
            if row is None or (row.scope_ref or GENERAL_SKILL_SCOPE) != scope_ref:
                return False
            if expected_file_id is not None and row.platform_file_id != expected_file_id:
                raise ReferenceConflict("Skill changed during deletion; refresh and retry")
            references = await db.execute(select(Agent.skills))
            if any(code in (skills or []) for skills in references.scalars()):
                raise ReferenceConflict("Skill is still referenced by Agents")
            if config is not None:
                name = skill_package_code(row)
                if name not in config.settings["skills"]:
                    return False
                if name in await other_app_skill_references(db, config):
                    raise ReferenceConflict("该 Skill 仍被其他集成引用。")
                config.settings = {**config.settings, "skills": [
                    item for item in config.settings["skills"] if item != name
                ]}
            result = await db.execute(delete(SkillPackage).where(SkillPackage.code == code))
            await db.flush()
            return result.rowcount > 0
