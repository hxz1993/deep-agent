from __future__ import annotations

import asyncio
from dataclasses import asdict

from a2a import types as wire
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from google.protobuf.json_format import MessageToDict
from sqlalchemy import and_, select

from deep_agent.agent.input_capabilities import document_upload_mimes
from deep_agent.agent.media_pipeline import allowed_mimes
from deep_agent.agent_service.download import ArtifactDownloadResponse
from deep_agent.agent_service.protocol import PublicationHandler, agent_card
from deep_agent.agent_service.publications import AgentCode, PublicationCreate, PublicationPatch, PublicationStore, ScopeId
from deep_agent.agent_service.security import observe_access, platform_caller, service_base_url
from deep_agent.agent_service.service import AgentService, bounded_body, task_store
from deep_agent.agent_service.tasks import TERMINAL
from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.core.context import AppContextDep
from deep_agent.db.models import ChatSession, ChatSessionEvent
from deep_agent.db.reference_integrity import ReferenceConflict
from deep_agent.documents.settings import load_document_parsing_config
from deep_agent.security.diagnostic_users import DiagnosticUserAuthenticationError, resolve_diagnostic_users
from deep_agent.services.integration_app_access import IntegrationAppAccessError, IntegrationAppDirectory
from deep_agent.services.scope_names import resolve_scope_directory

router = APIRouter(prefix="/agent-service")
tag = "agent-service"


def address_for(base: str, agent_code: str, app_id: str | None = None) -> str:
    address = f"{base}/api/v1/agent-service/a2a/{agent_code}"
    return f"{address}/apps/{app_id}" if app_id is not None else address


admin = CONSOLE_ADMIN_DEPENDENCIES


async def publication(ctx, agent_code: str):
    return await AgentService(ctx).publication(agent_code)


def serialize_publication(row, base: str | None) -> dict:
    card_url = app_card_url_template = None
    if row.enabled and base:
        card_url = address_for(base, row.code) + "/agent-card.json"
        app_card_url_template = address_for(base, row.code, "{app_id}") + "/agent-card.json"
    return {
        **{key: getattr(row, key) for key in (
            "id", "name", "description", "agent_id", "scope", "enabled",
            "revision", "created_at", "updated_at",
        )},
        "agent_code": row.code,
        "card_url": card_url,
        "app_card_url_template": app_card_url_template,
    }


@router.get("/publications", dependencies=admin)
async def list_publications(ctx: AppContextDep, request: Request, user: RequestUserDep):
    rows = await PublicationStore(ctx.db_session_factory).list()
    base = await ctx.agent_service_address.for_admin(request, user) if any(row.enabled for row in rows) else None
    return [serialize_publication(row, base) for row in rows]


@router.get("/applications", dependencies=admin)
async def list_apps(request: Request, user: RequestUserDep):
    try:
        return [asdict(app) for app in await IntegrationAppDirectory(request, user).available_apps()]
    except IntegrationAppAccessError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.get("/publications/{agent_code}", dependencies=admin)
async def get_publication(ctx: AppContextDep, agent_code: AgentCode, request: Request, user: RequestUserDep):
    row = await publication(ctx, agent_code)
    return await publication_response(ctx, row, request, user)


async def publication_response(ctx, row, request: Request, user: str) -> dict:
    base = await ctx.agent_service_address.for_admin(request, user) if row.enabled else None
    return serialize_publication(row, base)


@router.post("/publications", status_code=201, dependencies=admin)
async def create_publication(ctx: AppContextDep, body: PublicationCreate, request: Request, user: RequestUserDep):
    try:
        async with task_store(ctx).admission:
            row = await PublicationStore(ctx.db_session_factory).create(body)
    except ReferenceConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    return await publication_response(ctx, row, request, user)


@router.patch("/publications/{agent_code}", dependencies=admin)
async def edit_publication(ctx: AppContextDep, agent_code: AgentCode, body: PublicationPatch, request: Request, user: RequestUserDep):
    async with task_store(ctx).admission:
        try:
            row = await PublicationStore(ctx.db_session_factory).update(agent_code, body)
        except ReferenceConflict as exc:
            raise HTTPException(409, str(exc)) from exc
    if row is None:
        raise HTTPException(404, "发布不存在")
    return await publication_response(ctx, row, request, user)


@router.delete("/publications/{agent_code}", status_code=204, dependencies=admin)
async def delete_publication(ctx: AppContextDep, agent_code: AgentCode, revision: int = Query(ge=1)):
    tasks = task_store(ctx)
    async with tasks.admission:
        row = await publication(ctx, agent_code)
        tasks.prune()
        if any(entry.context.owner.publication_id == row.id and entry.state not in TERMINAL
               for entry in tasks.entries.values()):
            raise HTTPException(409, "请等待或取消尚未结束的任务")
        try:
            await PublicationStore(ctx.db_session_factory).delete(agent_code, revision)
        except ReferenceConflict as exc:
            raise HTTPException(409, str(exc)) from exc
    return Response(status_code=204)


def summary(entry):
    return {
        "id": entry.task.id, "context_id": entry.context.id, "title": entry.title,
        "app_id": entry.context.owner.app, "space_id": entry.context.owner.space,
        "caller": entry.context.owner.user, "source": entry.source,
        "status": wire.TaskState.Name(entry.state),
        "created_at": entry.created, "updated_at": entry.changed,
        "artifact_count": len(entry.files),
        "duration_seconds": round((entry.changed - entry.created).total_seconds(), 1) if entry.state in TERMINAL else None,
    }


async def caller_profiles(request: Request, user: str, user_ids: set[str]):
    try:
        return await resolve_diagnostic_users(request, user, user_ids)
    except DiagnosticUserAuthenticationError:
        # Names are optional. The Console management authorization is checked separately.
        return {}


@router.get("/publications/{agent_code}/tasks", dependencies=admin)
async def recent_tasks(ctx: AppContextDep, request: Request, user: RequestUserDep,
                       agent_code: AgentCode, page: int = Query(1, ge=1),
                       q: str = Query("", max_length=100), state: str = Query("", max_length=40),
                       app_id: ScopeId | None = None):
    row = await publication(ctx, agent_code)
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
    items = [summary(entry) for entry in rows[(page - 1) * 20:page * 20]]
    profiles = await caller_profiles(request, user, {item["caller"] for item in items})
    for item in items:
        item["caller_username"] = profiles.get(item["caller"], {}).get("username") or None
    return {
        "items": items,
        "total": len(rows),
        "app_ids": sorted({entry.context.owner.app for entry in entries}),
    }


@router.get("/publications/{agent_code}/tasks/{task_id}", dependencies=admin)
async def recent_task(ctx: AppContextDep, request: Request, user: RequestUserDep, agent_code: AgentCode, task_id: str):
    row = await publication(ctx, agent_code)
    tasks = task_store(ctx)
    tasks.prune()
    entry = tasks.entries.get(task_id)
    if entry is None or entry.context.owner.publication_id != row.id:
        raise HTTPException(404, "任务不存在")
    scope = entry.context
    # Keep the task state and its run IDs from the same point in time.
    run_ids = tuple(entry.run_ids)
    result = {
        **summary(entry),
        "client": entry.client,
        "message": [part.text for part in entry.task.status.message.parts if part.HasField("text")],
        "progress": [{"status": wire.TaskState.Name(state), "timestamp": stamp} for state, stamp in entry.progress],
        "files": [{"name": file.name, "mime": file.mime, "size": file.size} for file in entry.files.values()],
    }
    # A context allocates a session ID before execution. Only show a link
    # once the session exists, and only show turns persisted for this task.
    statement = select(
        ChatSession.session_id, ChatSession.title, ChatSessionEvent.turn_id, ChatSessionEvent.event_id,
        ChatSessionEvent.created_at, ChatSessionEvent.payload,
    ).outerjoin(
        ChatSessionEvent,
        and_(
            ChatSessionEvent.session_id == ChatSession.session_id,
            ChatSessionEvent.event_type == "user/message",
            ChatSessionEvent.run_id.in_(run_ids),
        ),
    ).where(
        ChatSession.session_id == scope.session_id,
        ChatSession.source_type == scope.source,
        ChatSession.source_id == row.id,
    ).order_by(ChatSessionEvent.sequence)
    async with ctx.db_session_factory() as db:
        links = (await db.execute(statement)).all()
    turns = {}
    for link in links:
        if link.turn_id:
            parts = (link.payload or {}).get("message", {}).get("parts", [])
            content = next((part.get("content") for part in parts if part.get("part_kind") == "user-prompt"), None)
            text = content if isinstance(content, str) else " ".join(
                item for item in (content or []) if isinstance(item, str)
            )
            turns.setdefault(link.turn_id, {
                "id": link.turn_id, "event_id": link.event_id,
                "created_at": link.created_at, "summary": " ".join(text.split())[:140],
            })
    profiles, names = await asyncio.gather(
        caller_profiles(request, user, {scope.owner.user}),
        resolve_scope_directory(request, user),
    )
    return {
        **result,
        "session_id": links[0].session_id if links else None,
        "session_title": links[0].title if links else None,
        "turns": list(turns.values()),
        "caller_username": profiles.get(scope.owner.user, {}).get("username") or None,
        "app_name": names.apps.get(scope.owner.app),
        "space_name": names.spaces.get(scope.owner.space),
    }


async def authorized_publication(ctx, agent_code: str, request: Request, app_id: str | None):
    row = await publication(ctx, agent_code)
    with observe_access("a2a", row.id):
        headers = await platform_caller(request, path_app=app_id)
        return row, AgentService.owner(row, headers), headers


async def a2a_address(ctx, request: Request, row, app_id: str | None, headers: dict[str, str]) -> str:
    with observe_access("a2a", row.id, phase="public_address"):
        base = await ctx.agent_service_address.resolve(request, service_base_url(request), headers)
        if base is None:
            raise HTTPException(503, "无法从当前请求解析 Agent 接入地址")
        return address_for(base, row.code, app_id)


async def _get_card(ctx: AppContextDep, agent_code: AgentCode, request: Request, app_id: str | None = None):
    row, _, headers = await authorized_publication(ctx, agent_code, request, app_id)
    if not row.enabled:
        raise HTTPException(404, "发布不存在")
    config = await load_document_parsing_config(ctx.stores.global_setting)
    address = await a2a_address(ctx, request, row, app_id, headers)
    card = agent_card(row, address, attachment_mimes=allowed_mimes(document_upload_mimes(config)))
    return JSONResponse(MessageToDict(card), headers={"Cache-Control": "no-store"})


async def _rpc(ctx: AppContextDep, agent_code: AgentCode, request: Request, app_id: str | None = None):
    row, owner, headers = await authorized_publication(ctx, agent_code, request, app_id)
    body = await bounded_body(request)
    body_pending = True

    async def receive():
        nonlocal body_pending
        if body_pending:
            body_pending = False
            return {"type": "http.request", "body": bytes(body), "more_body": False}
        return await request.receive()

    address = await a2a_address(ctx, request, row, app_id, headers)
    handler = PublicationHandler(ctx, task_store(ctx), row.code, owner, headers, address)
    dispatcher = JsonRpcDispatcher(handler, enable_v0_3_compat=False)
    response = await dispatcher.handle_requests(Request(request.scope, receive))
    response.headers["Cache-Control"] = "no-store"
    return response


async def _download_artifact(ctx: AppContextDep, agent_code: AgentCode, task_id: str, artifact_id: str, request: Request, app_id: str | None = None):
    _, owner, headers = await authorized_publication(ctx, agent_code, request, app_id)
    try:
        entry = task_store(ctx).get(owner, task_id)
    except wire.TaskNotFoundError as exc:
        raise HTTPException(404, "文件不存在或已过期") from exc
    service = AgentService(ctx)
    artifact = service.artifact(entry, artifact_id)
    handle = await service.open_artifact(entry, artifact, headers, protocol="a2a")
    return ArtifactDownloadResponse(entry, artifact, handle, protocol="a2a")


_A2A_PATH = "/a2a/{agent_code}"
_A2A_APP_PATH = _A2A_PATH + "/apps/{app_id}"
_ARTIFACT_PATH = "/tasks/{task_id}/artifacts/{artifact_id}/download"


@router.get(f"{_A2A_PATH}/.well-known/agent-card.json", include_in_schema=False)
@router.get(f"{_A2A_PATH}/agent-card.json")
async def get_card(ctx: AppContextDep, agent_code: AgentCode, request: Request):
    return await _get_card(ctx, agent_code, request)


@router.get(f"{_A2A_APP_PATH}/.well-known/agent-card.json", include_in_schema=False)
@router.get(f"{_A2A_APP_PATH}/agent-card.json")
async def get_app_card(ctx: AppContextDep, agent_code: AgentCode, app_id: str, request: Request):
    return await _get_card(ctx, agent_code, request, app_id)


@router.post(_A2A_PATH)
async def rpc(ctx: AppContextDep, agent_code: AgentCode, request: Request):
    return await _rpc(ctx, agent_code, request)


@router.post(_A2A_APP_PATH)
async def app_rpc(ctx: AppContextDep, agent_code: AgentCode, app_id: str, request: Request):
    return await _rpc(ctx, agent_code, request, app_id)


@router.get(_A2A_PATH + _ARTIFACT_PATH)
async def download_artifact(ctx: AppContextDep, agent_code: AgentCode, task_id: str, artifact_id: str, request: Request):
    return await _download_artifact(ctx, agent_code, task_id, artifact_id, request)


@router.get(_A2A_APP_PATH + _ARTIFACT_PATH)
async def download_app_artifact(
    ctx: AppContextDep, agent_code: AgentCode, app_id: str, task_id: str, artifact_id: str, request: Request,
):
    return await _download_artifact(ctx, agent_code, task_id, artifact_id, request, app_id)
