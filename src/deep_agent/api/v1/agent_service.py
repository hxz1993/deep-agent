from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from urllib.parse import quote

from a2a import types as wire
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from google.protobuf.json_format import MessageToDict
from starlette.background import BackgroundTask

from deep_agent.agent.input_capabilities import document_upload_mimes
from deep_agent.agent.media_pipeline import allowed_mimes
from deep_agent.agent_service.execution import MAX_INPUT_BYTES, permitted_agent
from deep_agent.agent_service.protocol import PublicationHandler, agent_card
from deep_agent.agent_service.publications import Code, PublicationCreate, PublicationPatch, PublicationScope, PublicationStore, ScopeId
from deep_agent.agent_service.security import platform_caller, public_base_url
from deep_agent.agent_service.tasks import Owner, TERMINAL, TaskStore
from deep_agent.api.v1.admin import require_console_admin
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.core.context import AppContextDep
from deep_agent.db.reference_integrity import ReferenceConflict
from deep_agent.documents.settings import load_document_parsing_config
from deep_agent.files.storage import FileStoreError, FileStoreNotFoundError, FileStorePermissionError
from deep_agent.services.integration_app_access import IntegrationAppAccessError, IntegrationAppDirectory

router = APIRouter(prefix="/agent-service")
tag = "agent-service"
MAX_BODY_BYTES = MAX_INPUT_BYTES * 4 // 3 + 256 * 1024


def task_store(ctx) -> TaskStore:
    if ctx.agent_tasks is None:
        ctx.agent_tasks = TaskStore()
    return ctx.agent_tasks


def address_for(code: str) -> str:
    try:
        base = public_base_url(os.environ.get("DEEP_AGENT_AGENT_SERVICE_PUBLIC_BASE_URL", ""))
    except ValueError as exc:
        raise HTTPException(503, "请配置 Agent Service 对外地址") from exc
    return f"{base}/api/v1/agent-service/a2a/{code}"


async def require_publication_admin(user: RequestUserDep) -> str:
    admins = {value.strip() for value in os.getenv("DEEP_AGENT_AGENT_SERVICE_ADMIN_USERS", "").split(",")}
    if user not in admins:
        raise HTTPException(403, "当前账号没有 Agent 发布管理权限")
    return user


admin = (Depends(require_console_admin), Depends(require_publication_admin))


async def publication(ctx, code: str):
    row = await PublicationStore(ctx.db_session_factory).get(code)
    if row is None:
        raise HTTPException(404, "发布不存在")
    return row


def serialize_publication(row) -> dict:
    card_url = None
    if row.enabled:
        try:
            card_url = address_for(row.code) + "/agent-card.json"
        except HTTPException as exc:
            if exc.status_code != 503:
                raise
    return {
        **{key: getattr(row, key) for key in (
            "id", "code", "name", "description", "agent_id", "scope", "enabled",
            "revision", "created_at", "updated_at",
        )},
        "card_url": card_url,
    }


async def verify_scopes(request: Request, user: str, scopes: list[PublicationScope]):
    selected = {(item.app_id, item.space_id) for scope in scopes for item in scope.apps.items}
    if not selected:
        return
    try:
        directory = IntegrationAppDirectory(request, user)
        available = {(app.app_id, app.space_id) for app in await directory.available_apps()}
    except IntegrationAppAccessError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    if not selected <= available:
        raise HTTPException(403, "指定应用不在当前账号的可配置范围内")


@router.get("/publications", dependencies=admin)
async def list_publications(ctx: AppContextDep):
    return [serialize_publication(row) for row in await PublicationStore(ctx.db_session_factory).list()]


@router.get("/applications", dependencies=admin)
async def list_apps(request: Request, user: RequestUserDep):
    try:
        return [asdict(app) for app in await IntegrationAppDirectory(request, user).available_apps()]
    except IntegrationAppAccessError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.get("/publications/{code}", dependencies=admin)
async def get_publication(ctx: AppContextDep, code: Code):
    return serialize_publication(await publication(ctx, code))


@router.post("/publications", status_code=201, dependencies=admin)
async def create_publication(ctx: AppContextDep, request: Request, user: RequestUserDep, body: PublicationCreate):
    address_for(body.code)
    await permitted_agent(ctx, body.agent_id)
    await verify_scopes(request, user, [body.scope])
    try:
        async with task_store(ctx).admission:
            return serialize_publication(await PublicationStore(ctx.db_session_factory).create(body))
    except ReferenceConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.patch("/publications/{code}", dependencies=admin)
async def edit_publication(ctx: AppContextDep, request: Request, user: RequestUserDep, code: Code, body: PublicationPatch):
    if body.scope:
        await verify_scopes(request, user, [body.scope])
    if body.enabled is True:
        address_for(code)
    async with task_store(ctx).admission:
        current = await publication(ctx, code)
        if body.agent_id or body.enabled is True:
            await permitted_agent(ctx, body.agent_id or current.agent_id)
        try:
            row = await PublicationStore(ctx.db_session_factory).update(code, body)
        except ReferenceConflict as exc:
            raise HTTPException(409, str(exc)) from exc
    if row is None:
        raise HTTPException(404, "发布不存在")
    return serialize_publication(row)


@router.delete("/publications/{code}", status_code=204, dependencies=admin)
async def delete_publication(ctx: AppContextDep, code: Code, revision: int = Query(ge=1)):
    tasks = task_store(ctx)
    async with tasks.admission:
        row = await publication(ctx, code)
        tasks.prune()
        if any(entry.context.owner.publication_id == row.id and entry.state not in TERMINAL
               for entry in tasks.entries.values()):
            raise HTTPException(409, "请等待或取消尚未结束的任务")
        try:
            await PublicationStore(ctx.db_session_factory).delete(code, revision)
        except ReferenceConflict as exc:
            raise HTTPException(409, str(exc)) from exc
    return Response(status_code=204)


def summary(entry):
    return {
        "id": entry.task.id, "context_id": entry.context.id, "title": entry.title,
        "app_id": entry.context.owner.app, "space_id": entry.context.owner.space,
        "caller": entry.context.owner.user,
        "status": wire.TaskState.Name(entry.state),
        "created_at": entry.created, "updated_at": entry.changed,
        "artifact_count": len(entry.files),
        "duration_seconds": round((entry.changed - entry.created).total_seconds(), 1) if entry.state in TERMINAL else None,
    }


@router.get("/publications/{code}/tasks", dependencies=admin)
async def recent_tasks(ctx: AppContextDep, code: Code, page: int = Query(1, ge=1),
                       q: str = Query("", max_length=100), state: str = Query("", max_length=40),
                       app_id: ScopeId | None = None):
    row = await publication(ctx, code)
    tasks = task_store(ctx)
    tasks.prune()
    entries = [entry for entry in tasks.entries.values() if entry.context.owner.publication_id == row.id]
    query = q.strip().casefold()
    rows = sorted((
        entry for entry in entries
        if (not app_id or entry.context.owner.app == app_id)
        and (not query or query in entry.title.casefold() or query in entry.task.id)
        and (not state or wire.TaskState.Name(entry.state) == state)
    ), key=lambda entry: entry.created, reverse=True)
    return {
        "items": [summary(entry) for entry in rows[(page - 1) * 20:page * 20]],
        "total": len(rows),
        "app_ids": sorted({entry.context.owner.app for entry in entries}),
    }


@router.get("/publications/{code}/tasks/{task_id}", dependencies=admin)
async def recent_task(ctx: AppContextDep, code: Code, task_id: str):
    row = await publication(ctx, code)
    tasks = task_store(ctx)
    tasks.prune()
    entry = tasks.entries.get(task_id)
    if entry is None or entry.context.owner.publication_id != row.id:
        raise HTTPException(404, "任务不存在")
    return {
        **summary(entry), "session_id": entry.context.session_id,
        "message": [part.text for part in entry.task.status.message.parts if part.HasField("text")],
        "progress": [{"status": wire.TaskState.Name(state), "timestamp": stamp} for state, stamp in entry.progress],
        "files": [{"name": file.name, "mime": file.mime, "size": file.size} for file in entry.files.values()],
    }


async def authorized_publication(ctx, code: str, request: Request):
    row = await publication(ctx, code)
    scope = PublicationScope.model_validate(row.scope)
    headers = await platform_caller(request, fixed_app=scope.fixed_app)
    if not scope.allows(headers["app"], headers["space"]):
        raise HTTPException(403, "app Header 指定的应用不在该发布的允许范围内，请检查发布配置和 App ID。")
    return row, Owner(row.id, headers["user"], headers["app"], headers["space"]), headers


@router.get("/a2a/{code}/.well-known/agent-card.json", include_in_schema=False)
@router.get("/a2a/{code}/agent-card.json")
async def get_card(ctx: AppContextDep, code: Code, request: Request):
    row, _, _ = await authorized_publication(ctx, code, request)
    if not row.enabled:
        raise HTTPException(404, "发布不存在")
    config = await load_document_parsing_config(ctx.stores.global_setting)
    card = agent_card(row, address_for(row.code), attachment_mimes=allowed_mimes(document_upload_mimes(config)))
    return JSONResponse(MessageToDict(card), headers={"Cache-Control": "no-store"})


@router.post("/a2a/{code}")
async def rpc(ctx: AppContextDep, code: Code, request: Request):
    row, owner, headers = await authorized_publication(ctx, code, request)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise HTTPException(413, "A2A request exceeds the size limit")
        body.extend(chunk)
    body_pending = True

    async def receive():
        nonlocal body_pending
        if body_pending:
            body_pending = False
            return {"type": "http.request", "body": bytes(body), "more_body": False}
        return await request.receive()

    handler = PublicationHandler(
        ctx, task_store(ctx), PublicationStore(ctx.db_session_factory),
        row.code, owner, headers, address_for(row.code),
    )
    dispatcher = JsonRpcDispatcher(handler, enable_v0_3_compat=False)
    response = await dispatcher.handle_requests(Request(request.scope, receive))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/a2a/{code}/tasks/{task_id}/artifacts/{artifact_id}/download")
async def download_artifact(ctx: AppContextDep, code: Code, task_id: str, artifact_id: str, request: Request):
    _, owner, headers = await authorized_publication(ctx, code, request)
    try:
        entry = task_store(ctx).get(owner, task_id)
    except wire.TaskNotFoundError as exc:
        raise HTTPException(404, "文件不存在或已过期") from exc
    artifact = entry.files.get(artifact_id)
    if artifact is None:
        raise HTTPException(404, "文件不存在或已过期")
    response_headers = {
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(artifact.name, safe=""),
        "Content-Length": str(artifact.size), "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    }
    try:
        handle = await ctx.file_store.open_download(artifact.id, headers, expected_size=artifact.size)
    except (FileStorePermissionError, FileStoreNotFoundError) as exc:
        raise HTTPException(404, "文件不存在或已过期") from exc
    except FileStoreError as exc:
        raise HTTPException(502, "文件暂不可读取") from exc
    async def chunks():
        try:
            while chunk := await asyncio.to_thread(handle.read, 64 * 1024):
                yield chunk
        finally:
            handle.close()

    return StreamingResponse(
        chunks(), media_type=artifact.mime, headers=response_headers, background=BackgroundTask(handle.close),
    )
