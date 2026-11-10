"""Manage spreadsheet-only app instructions and scoped Skill packages."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.api.v1.integration_routes import IntegrationRoute
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.core.context import AppContextDep
from deep_agent.db.models import IntegrationScopedSettings
from deep_agent.db.reference_integrity import ReferenceConflict
from deep_agent.integrations.app_scope import APP_SKILL_SCOPE_PREFIX, GENERAL_SKILL_SCOPE, INTEGRATION, LEGACY_SKILL_SCOPE_PREFIX, app_skill_scope
from deep_agent.services.integration_app_access import IntegrationAppAccessError, IntegrationAppDirectory, ManagedApp
from deep_agent.services.scope_names import resolve_scope_directory

APP_CONFIG_PATH = f"/integrations/{INTEGRATION}/app-configs"
router = APIRouter(prefix=APP_CONFIG_PATH, dependencies=CONSOLE_ADMIN_DEPENDENCIES, route_class=IntegrationRoute)
tag = "integrations"


class AppConfigSummary(BaseModel):
    config_id: str | None
    app_id: str
    app_name: str
    space_id: str
    space_name: str
    tenant_id: str
    tenant_name: str
    skill_count: int = 0


class AppConfigDetail(AppConfigSummary):
    prompt_append: str = ""


class AppConfigWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_id: str | None = Field(default=None, min_length=1, max_length=36)
    prompt_append: str = Field(max_length=4000)


@dataclass(frozen=True)
class AppConfigAccess:
    app: ManagedApp
    row: IntegrationScopedSettings | None

    @property
    def skill_scope(self) -> str | None:
        return app_skill_scope(self.app.app_id) if self.row is not None else None

    @property
    def skill_codes(self) -> tuple[str, ...]:
        return tuple(self.row.settings["skills"]) if self.row is not None else ()


def _store(ctx):
    if ctx.stores.integration_app is None:
        raise HTTPException(503, detail="应用配置服务暂不可用。")
    return ctx.stores.integration_app


async def verify_app_scope_rows(request: Request, user: str, rows, *, skill_scopes=()) -> None:
    owners: dict[str, set[tuple[str, str]]] = {}
    legacy_owners = set()
    for row in rows:
        space_id, _, app_id = row["scope_key"].partition("/")
        owners.setdefault(app_id, set()).add((space_id, row["tenant_id"]))
        legacy_owners.add(LEGACY_SKILL_SCOPE_PREFIX + row["id"])
    for scope in skill_scopes:
        if scope in (None, GENERAL_SKILL_SCOPE) or scope in legacy_owners:
            continue
        if not scope.startswith(APP_SKILL_SCOPE_PREFIX):
            raise HTTPException(403, detail="无法验证备份中的应用归属。")
        owners.setdefault(scope.removeprefix(APP_SKILL_SCOPE_PREFIX), set())
    if not owners:
        return
    try:
        directory = IntegrationAppDirectory(request, user)
        for app_id, expected in owners.items():
            app = await directory.verify(app_id)
            if app.app_id != app_id or expected - {(app.space_id, app.tenant_id)}:
                raise IntegrationAppAccessError(403, "无法验证备份中的应用归属。")
    except IntegrationAppAccessError as exc:
        raise HTTPException(exc.status, detail=str(exc)) from exc


async def app_config_access(app_id: str, request: Request, user: RequestUserDep, ctx: AppContextDep) -> AppConfigAccess:
    try:
        app = await IntegrationAppDirectory(request, user).verify(app_id)
    except IntegrationAppAccessError as exc:
        raise HTTPException(exc.status, detail=str(exc)) from exc
    row = await _store(ctx).get(app.app_id, app.space_id)
    if row is not None and row.tenant_id != app.tenant_id:
        raise HTTPException(404, detail="应用配置不存在或当前账号无权管理。")
    access = AppConfigAccess(app, row)
    request.state.integration_app_access = access
    return access


AppConfigAccessDep = Annotated[AppConfigAccess, Depends(app_config_access)]


@router.get("", response_model=list[AppConfigSummary])
async def list_app_configs(request: Request, user: RequestUserDep, ctx: AppContextDep) -> list[AppConfigSummary]:
    try:
        directory = IntegrationAppDirectory(request, user)
        tenants = await directory.tenants()
        rows = await _store(ctx).list_for_tenants(set(tenants))
        if not rows:
            return []
        names = await resolve_scope_directory(request, user)

        async def describe(row, count):
            space_id, _, app_id = row.scope_key.partition("/")
            try:
                app = await directory.verify(app_id)
            except IntegrationAppAccessError as exc:
                if exc.status in (403, 404):
                    return None
                raise
            if app.space_id != space_id or app.tenant_id != row.tenant_id:
                return None
            facts = asdict(app)
            facts["space_name"] = names.spaces.get(space_id, app.space_name)
            return AppConfigSummary(**facts, config_id=row.id, skill_count=count)

        items = await asyncio.gather(*(describe(row, count) for row, count in rows))
        return [item for item in items if item is not None]
    except IntegrationAppAccessError as exc:
        raise HTTPException(exc.status, detail=str(exc)) from exc


@router.get("/available", response_model=list[ManagedApp])
async def list_available_apps(request: Request, user: RequestUserDep) -> list[ManagedApp]:
    """List applications visible to the caller in enterprises they administer."""
    try:
        return await IntegrationAppDirectory(request, user).available_apps()
    except IntegrationAppAccessError as exc:
        raise HTTPException(exc.status, detail=str(exc)) from exc


@router.get("/{app_id}", response_model=AppConfigDetail)
async def get_app_config(
    access: AppConfigAccessDep, request: Request, user: RequestUserDep, ctx: AppContextDep,
) -> AppConfigDetail:
    row = access.row
    names = await resolve_scope_directory(request, user)
    facts = asdict(access.app)
    facts["space_name"] = names.spaces.get(access.app.space_id, access.app.space_name)
    return AppConfigDetail(
        **facts, config_id=row.id if row else None,
        skill_count=len(access.skill_codes),
        prompt_append=row.settings.get("prompt_append", "") if row else "",
    )


@router.put("/{app_id}", response_model=AppConfigDetail)
async def save_app_config(body: AppConfigWrite, access: AppConfigAccessDep, ctx: AppContextDep) -> AppConfigDetail:
    if body.config_id is not None and (access.row is None or access.row.id != body.config_id):
        raise HTTPException(409, detail="应用配置已变更，请刷新后重试。")
    store = _store(ctx)
    try:
        row = await store.save(
            access.app, body.prompt_append, expected_id=access.row.id if access.row else None,
        )
    except ReferenceConflict as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    return AppConfigDetail(
        **asdict(access.app), config_id=row.id, prompt_append=row.settings["prompt_append"],
        skill_count=len(row.settings["skills"]),
    )


@router.delete("/{app_id}")
async def delete_app_config(access: AppConfigAccessDep, ctx: AppContextDep, request: Request, config_id: str) -> dict:
    from deep_agent.api.v1.skills import cleanup_deleted_skill

    if access.row is None or access.row.id != config_id:
        raise HTTPException(409, detail="应用配置已变更，请刷新后重试。")
    try:
        removed = await _store(ctx).delete(access.app, expected_id=config_id)
    except ReferenceConflict as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    for skill in removed:
        await cleanup_deleted_skill(ctx, request, skill)
    return {"status": "deleted"}
