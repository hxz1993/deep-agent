from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from deep_agent.db.models import IntegrationScopedSettings, IntegrationSettings, SkillPackage
from deep_agent.db.reference_integrity import ReferenceConflict
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.integrations.app_scope import (
    INTEGRATION, LEGACY_SKILL_SCOPE_PREFIX, app_scope_key, app_skill_scope, skill_package_code,
)


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
        row = IntegrationScopedSettings(
            id=str(uuid4()), integration_code=INTEGRATION, scope_type="app",
            scope_key=key, tenant_id=app.tenant_id, settings={"prompt_append": "", "skills": []},
        )
        db.add(row)
        await db.flush()
    if row.scope_key != key or row.tenant_id != app.tenant_id:
        raise ReferenceConflict("应用所属企业已变更，无法沿用原配置。")
    return row


async def app_skill_rows(db, scope_ref: str, *, lock: bool = False) -> list[SkillPackage]:
    query = select(SkillPackage).where(SkillPackage.scope_ref == scope_ref).order_by(SkillPackage.label, SkillPackage.code)
    if lock:
        query = query.with_for_update()
    # Match the platform-verified ID exactly even on case-insensitive databases.
    return [row for row in await db.scalars(query) if row.scope_ref == scope_ref]


async def other_app_skill_references(db, config: IntegrationScopedSettings) -> set[str]:
    settings = await db.scalars(select(IntegrationScopedSettings.settings).where(
        IntegrationScopedSettings.scope_type == "app",
        IntegrationScopedSettings.scope_key == config.scope_key,
        IntegrationScopedSettings.id != config.id,
    ))
    return {code for value in settings for code in value.get("skills", ())}


class IntegrationAppStore(SessionBoundMixin):
    async def require_app_skill_conversion(self) -> None:
        async with self._read() as db:
            legacy_skill = await db.scalar(select(SkillPackage.code).where(
                SkillPackage.scope_ref.startswith(LEGACY_SKILL_SCOPE_PREFIX),
            ).limit(1))
            settings = await db.scalars(select(IntegrationScopedSettings.settings).where(
                IntegrationScopedSettings.integration_code == INTEGRATION,
                IntegrationScopedSettings.scope_type == "app",
            ))
            if legacy_skill is not None or any("skills" not in value for value in settings):
                raise RuntimeError(
                    "App Skill data needs conversion. Run python -m maintenance.app_skill_ownership --config <file> "
                    "to check, then add --apply after stopping old writers and backing up."
                )

    async def get(self, app_id: str, space_id: str) -> IntegrationScopedSettings | None:
        key = app_scope_key(app_id, space_id)
        async with self._read() as db:
            row = (await db.scalars(_select_scope(key))).one_or_none()
            return row if row is not None and row.scope_key == key else None

    async def get_for_agent(self, app_id: str, space_id: str, agent_id: str) -> IntegrationScopedSettings | None:
        key = app_scope_key(app_id, space_id)
        query = _select_scope(key).join(
            IntegrationSettings, IntegrationSettings.code == IntegrationScopedSettings.integration_code,
        ).add_columns(IntegrationSettings.settings)
        async with self._read() as db:
            match = (await db.execute(query)).one_or_none()
            if match is None:
                return None
            scope, binding = match
            return scope if scope.scope_key == key and binding.get("agent_id") == agent_id else None

    async def list_for_tenants(self, tenant_ids: set[str]) -> list[tuple[IntegrationScopedSettings, int]]:
        if not tenant_ids:
            return []
        row = IntegrationScopedSettings
        async with self._read() as db:
            rows = await db.scalars(select(row).where(
                row.integration_code == INTEGRATION, row.scope_type == "app", row.tenant_id.in_(tenant_ids),
            ).order_by(row.scope_key))
            return [(config, len(config.settings["skills"])) for config in rows]

    async def save(self, app, prompt_append: str, *, expected_id: str | None) -> IntegrationScopedSettings:
        async with self._write(exclusive=True) as db:
            row = await ensure_app_scope(db, app=app, expected_id=expected_id)
            row.settings = {**row.settings, "prompt_append": prompt_append}
            await db.flush()
            await db.refresh(row)
            return row

    async def delete(self, app, *, expected_id: str) -> list[SkillPackage]:
        async with self._write(exclusive=True) as db:
            row = await ensure_app_scope(db, app=app, expected_id=expected_id)
            exclusive = set(row.settings["skills"]) - await other_app_skill_references(db, row)
            removed = []
            if exclusive:
                for skill in await app_skill_rows(db, app_skill_scope(app.app_id), lock=True):
                    if skill_package_code(skill) in exclusive:
                        await db.delete(skill)
                        removed.append(skill)
            await db.delete(row)
            return removed
