from __future__ import annotations

from uuid import uuid4

from sqlalchemy import delete, func, select

from deep_agent.db.models import IntegrationScopedSettings, SkillPackage
from deep_agent.db.reference_integrity import ReferenceConflict
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.integrations.app_scope import INTEGRATION, app_scope_key, skill_scope_ref


def _select_scope(scope_key: str):
    row = IntegrationScopedSettings
    return select(row).where(row.integration_code == INTEGRATION, row.scope_type == "app", row.scope_key == scope_key)


async def ensure_app_scope(db, *, app, expected_id: str | None = None) -> IntegrationScopedSettings:
    """Caller holds the exclusive configuration write barrier."""
    key = app_scope_key(app.app_id, app.space_id)
    row = (await db.scalars(_select_scope(key).with_for_update())).one_or_none()
    if expected_id is not None and (row is None or row.id != expected_id):
        raise ReferenceConflict("应用配置已变更，请刷新后重试。")
    if row is None:
        row = IntegrationScopedSettings(id=str(uuid4()), integration_code=INTEGRATION, scope_type="app",
                                        scope_key=key, tenant_id=app.tenant_id, settings={"prompt_append": ""})
        db.add(row)
        await db.flush()
    if row.tenant_id != app.tenant_id:
        raise ReferenceConflict("应用所属企业已变更，无法沿用原配置。")
    return row


class IntegrationAppStore(SessionBoundMixin):
    async def get(self, app_id: str, space_id: str) -> IntegrationScopedSettings | None:
        async with self._read() as db:
            return (await db.scalars(_select_scope(app_scope_key(app_id, space_id)))).one_or_none()

    async def count_skills(self, scope_id: str) -> int:
        async with self._read() as db:
            return (await db.execute(
                select(func.count()).select_from(SkillPackage).where(
                    SkillPackage.scope_ref == skill_scope_ref(scope_id),
                )
            )).scalar_one()

    async def list_for_tenants(self, tenant_ids: set[str]) -> list[tuple[IntegrationScopedSettings, int]]:
        if not tenant_ids:
            return []
        row = IntegrationScopedSettings
        # Group counts once for all scopes rather than counting packages per App.
        counts = (select(SkillPackage.scope_ref, func.count().label("skill_count"))
                  .group_by(SkillPackage.scope_ref).subquery())
        async with self._read() as db:
            return list((await db.execute(
                select(row, func.coalesce(counts.c.skill_count, 0)).outerjoin(
                    counts, counts.c.scope_ref == (skill_scope_ref("") + row.id)
                ).where(row.integration_code == INTEGRATION, row.scope_type == "app",
                        row.tenant_id.in_(tenant_ids)).order_by(row.scope_key)
            )).all())

    async def save(self, app, prompt_append: str, *, expected_id: str | None) -> IntegrationScopedSettings:
        async with self._write(exclusive=True) as db:
            row = await ensure_app_scope(db, app=app, expected_id=expected_id)
            row.settings = {"prompt_append": prompt_append}
            await db.flush()
            await db.refresh(row)
            return row

    async def delete(self, app, *, expected_id: str) -> list[SkillPackage]:
        async with self._write(exclusive=True) as db:
            row = await ensure_app_scope(db, app=app, expected_id=expected_id)
            condition = SkillPackage.scope_ref == skill_scope_ref(row.id)
            skills = list((await db.scalars(select(SkillPackage).where(condition).with_for_update())).all())
            await db.execute(delete(SkillPackage).where(condition))
            await db.delete(row)
            return skills
