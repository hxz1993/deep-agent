from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.api.v1.channel_auth import ChannelAccountDep
from deep_agent.core.context import AppContextDep
from deep_agent.schemas.llm_provider import mask_api_key
from deep_agent.services.wecom_client import WecomError


class WecomRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handle(request):
            try:
                response = await original(request)
            except RequestValidationError:
                response = JSONResponse({"detail": "企微配置或查询参数无效，请检查必填项和字段格式"}, status_code=422)
            except WecomError as exc:
                response = JSONResponse({"detail": str(exc)}, status_code=503)
            except HTTPException as exc:
                exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
                raise
            response.headers["Cache-Control"] = "no-store"
            return response
        return handle


router = APIRouter(route_class=WecomRoute)


class ApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=191)
    corp_id: str = Field(min_length=1, max_length=128)
    wecom_agent_id: str = Field(min_length=1, max_length=128)
    app_secret: str = Field(min_length=1, max_length=1024)
    settings: dict[str, Any] = Field(default_factory=dict)


class ApplicationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=1, max_length=191)
    # Identity is accepted only to verify an unchanged readonly form value.
    corp_id: str | None = Field(default=None, min_length=1, max_length=128)
    wecom_agent_id: str | None = Field(default=None, min_length=1, max_length=128)
    app_secret: str | None = Field(default=None, max_length=1024)
    clear_app_secret: bool = False
    settings: dict[str, Any] | None = None


class CacheMember(BaseModel):
    id: str
    name: str | None
    userid: str
    open_userid: str | None
    profile_synced_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    last_error_stage: str | None
    last_error_code: str | None
    status: Literal["valid", "stale"]


class CacheMembersPage(BaseModel):
    items: list[CacheMember]
    total: int
    limit: int
    offset: int


class ResolveUser(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sender_id: str = Field(min_length=1, max_length=512)
    peer_type: str = Field(default="single", pattern="^(single|group)$")
    peer_id: str | None = Field(default=None, max_length=512)
    session_id: str | None = Field(default=None, max_length=128)


async def _get(ctx, application_id):
    row = await ctx.stores.wecom.get_application(application_id)
    if row is None:
        raise HTTPException(404, "企微应用不存在")
    return row


async def _response(ctx, row):
    channels = [
        {"id": account.id, "name": account.name}
        for account in await ctx.stores.channel.list_accounts()
        if isinstance((account.settings or {}).get("wecom"), dict)
        and account.settings["wecom"].get("application_id") == row.id
    ]
    return {
        "id": row.id, "name": row.name, "corp_id": row.corp_id,
        "wecom_agent_id": row.wecom_agent_id,
        "app_secret_masked": mask_api_key(row.app_secret), "secret_set": bool(row.app_secret),
        "settings": row.settings or {}, "revision": row.revision,
        "created_at": row.created_at, "updated_at": row.updated_at,
        "channel_count": len(channels),
        "channels": channels,
    }


@router.get("/wecom-applications", dependencies=CONSOLE_ADMIN_DEPENDENCIES)
async def list_applications(ctx: AppContextDep):
    return [await _response(ctx, row) for row in await ctx.stores.wecom.list_applications()]


@router.post("/wecom-applications", status_code=201, dependencies=CONSOLE_ADMIN_DEPENDENCIES)
async def create_application(body: ApplicationCreate, ctx: AppContextDep):
    try:
        row = await ctx.stores.wecom.create_application(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return await _response(ctx, row)


@router.get("/wecom-applications/{application_id}", dependencies=CONSOLE_ADMIN_DEPENDENCIES)
async def get_application(application_id: str, ctx: AppContextDep):
    return await _response(ctx, await _get(ctx, application_id))


@router.put("/wecom-applications/{application_id}", dependencies=CONSOLE_ADMIN_DEPENDENCIES)
async def update_application(application_id: str, body: ApplicationUpdate, ctx: AppContextDep):
    current = await _get(ctx, application_id)
    updates = body.model_dump(exclude_unset=True)
    for field in ("corp_id", "wecom_agent_id"):
        if field in updates and updates[field] != getattr(current, field):
            raise HTTPException(422, "企业 ID 和企微 Agent ID 创建后不可修改")
        updates.pop(field, None)
    if any(updates.get(key) is None for key in ("name", "settings") if key in updates):
        raise HTTPException(422, "名称和扩展配置不能为 null")
    if updates.get("clear_app_secret") and updates.get("app_secret") not in (None, ""):
        raise HTTPException(422, "清空凭据与设置新凭据不能同时提交")
    if updates.get("app_secret") in (None, "", mask_api_key(current.app_secret)):
        updates.pop("app_secret", None)
    try:
        row = await ctx.stores.wecom.update_application(application_id, **updates)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    if row is None:
        raise HTTPException(404, "企微应用不存在")
    if row.revision != current.revision:
        ctx.get_wecom_profiles().invalidate_runtime(application_id)
    return await _response(ctx, row)


@router.delete("/wecom-applications/{application_id}", dependencies=CONSOLE_ADMIN_DEPENDENCIES)
async def delete_application(application_id: str, ctx: AppContextDep):
    await _get(ctx, application_id)
    try:
        await ctx.stores.wecom.delete_application(application_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    ctx.get_wecom_profiles().invalidate_runtime(application_id)
    return {"deleted": True}


@router.post("/wecom-applications/{application_id}/test-connection", dependencies=CONSOLE_ADMIN_DEPENDENCIES)
async def test_connection(application_id: str, ctx: AppContextDep):
    row = await _get(ctx, application_id)
    try:
        await ctx.get_wecom_profiles().client.token(row, force=True)
    except WecomError as exc:
        return {"success": False, "message": str(exc)}
    return {"success": True, "message": "凭据有效；成员查询仍受应用可见范围和权限限制。"}


@router.get("/wecom-applications/{application_id}/cache", dependencies=CONSOLE_ADMIN_DEPENDENCIES)
async def cache_stats(application_id: str, ctx: AppContextDep):
    await _get(ctx, application_id)
    return await ctx.stores.wecom.cache_stats(application_id)


@router.get("/wecom-applications/{application_id}/cache/members", response_model=CacheMembersPage, dependencies=CONSOLE_ADMIN_DEPENDENCIES)
async def cache_members(
    application_id: str, ctx: AppContextDep,
    q: str = Query(default="", max_length=512),
    status: Literal["all", "valid", "stale"] = "all",
    synced: Literal["all", "day", "week", "older", "never"] = "all",
    sort: Literal["desc", "asc"] = "desc",
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    await _get(ctx, application_id)
    return await ctx.stores.wecom.list_cache_members(
        application_id, q=q, status=status, synced=synced, sort=sort, limit=limit, offset=offset,
    )


@router.post("/wecom-applications/{application_id}/cache/invalidate", dependencies=CONSOLE_ADMIN_DEPENDENCIES)
async def invalidate_cache(application_id: str, ctx: AppContextDep):
    await _get(ctx, application_id)
    await ctx.stores.wecom.invalidate_cache(application_id)
    ctx.get_wecom_profiles().invalidate_runtime(application_id)
    return await ctx.stores.wecom.cache_stats(application_id)


@router.post("/wecom-applications/{application_id}/cache/refresh", status_code=202, dependencies=CONSOLE_ADMIN_DEPENDENCIES)
async def refresh_cache(application_id: str, ctx: AppContextDep):
    await _get(ctx, application_id)
    return await ctx.get_wecom_profiles().start_refresh(application_id)


@router.get("/wecom-applications/{application_id}/cache/refresh/{task_id}", dependencies=CONSOLE_ADMIN_DEPENDENCIES)
async def refresh_status(application_id: str, task_id: str, ctx: AppContextDep):
    await _get(ctx, application_id)
    job = ctx.get_wecom_profiles().refresh_status(application_id, task_id)
    if job is None:
        raise HTTPException(404, "结果待确认，请重新读取缓存统计")
    return job


@router.post("/internal/channels/wecom/resolve-user")
async def resolve_user(body: ResolveUser, ctx: AppContextDep, account: ChannelAccountDep):
    if account is None:
        raise HTTPException(401, "Missing channel internal token")
    if account.channel_type != "wecom":
        raise HTTPException(422, "当前渠道不是企微渠道")
    if body.session_id and await ctx.stores.channel.get_channel_session(account.id, body.session_id) is None:
        raise HTTPException(403, "会话不属于当前渠道")
    return await ctx.get_wecom_profiles().resolve(account, body.sender_id,
        peer_type=body.peer_type, session_id=body.session_id)
