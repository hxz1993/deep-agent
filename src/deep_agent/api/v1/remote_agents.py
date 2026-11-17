from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from deep_agent.agent.headers import extract_execution_context_headers
from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.core.context import AppContextDep
from deep_agent.db.reference_integrity import ReferenceConflict
from deep_agent.remote_agents.contracts import REMOTE_AGENT_PROTOCOLS, ConnectionWrite, RemoteAgentError
from deep_agent.remote_agents.runtime import RequestScope
from deep_agent.remote_agents.store import UPDATED_BY_KEY
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.security.diagnostic_users import DiagnosticUserAuthenticationError, resolve_diagnostic_users

router = APIRouter(prefix="/remote-agent-connections", dependencies=CONSOLE_ADMIN_DEPENDENCIES)
tag = "remote-agents"


def response(row, agents: dict) -> dict:
    config = dict(row.config)
    if config.get("protocol") == "a2a":
        config["headers"] = dict.fromkeys(config.get("headers", {}))
    inspection = dict(row.inspection or {})
    updated_by = inspection.pop(UPDATED_BY_KEY, None)
    return {
        **{key: getattr(row, key) for key in ("id", "name", "description", "revision", "created_at", "updated_at")},
        "config": config, "inspection": inspection or None, "updated_by": updated_by, "agents": agents.get(row.id, []),
    }


async def required(runtime, connection_id):
    row = await runtime.connections.get(connection_id)
    if row is None or row.config.get("protocol") not in REMOTE_AGENT_PROTOCOLS:
        raise HTTPException(404, "远程 Agent 连接不存在")
    return row


def scope(request: Request, user: str, ctx: AppContextDep) -> RequestScope:
    headers = extract_execution_context_headers(request)
    headers["user"] = user
    return RequestScope(user, ctx.file_store, headers, app_id=headers.get("app"), space_id=headers.get("space"))


@router.get("")
async def list_connections(ctx: AppContextDep, request: Request, user: RequestUserDep):
    store = ctx.get_remote_agents().connections
    agents = await store.agents()
    rows = [response(row, agents) for row in await store.list() if row.config.get("protocol") in REMOTE_AGENT_PROTOCOLS]
    user_ids = {row["updated_by"] for row in rows if row["updated_by"]}
    if user_ids:
        try:
            profiles = await resolve_diagnostic_users(request, user, user_ids)
        except DiagnosticUserAuthenticationError:
            profiles = {}
        for row in rows:
            row["updated_by_username"] = profiles.get(row["updated_by"], {}).get("username")
    return rows


@router.get("/recent")
async def recent_connections(ctx: AppContextDep, request: Request, user: RequestUserDep):
    runtime = ctx.get_remote_agents()
    current = scope(request, user, ctx)
    entries = runtime.recent.list(current)
    links = await runtime.connections.agents()
    return [entry for entry in entries if any(item["id"] == entry["agent_id"]
            for item in links.get(entry["connection_id"], ()))]


@router.get("/{connection_id}")
async def get_connection(ctx: AppContextDep, connection_id: str):
    runtime = ctx.get_remote_agents()
    return response(await required(runtime, connection_id), await runtime.connections.agents())


async def save_connection(ctx, data: dict, user: str, connection_id: str | None = None):
    store = ctx.get_remote_agents().connections
    try:
        body = ConnectionWrite.model_validate(data)
        if connection_id and body.revision is None:
            raise HTTPException(422, "更新连接必须携带版本")
        row = await store.save(body, connection_id, updated_by=user)
    except ValidationError as error:
        issues = error.errors(include_context=False, include_input=False, include_url=False)
        raise HTTPException(422, [{"loc": item["loc"], "type": item["type"], "msg": item["msg"]} for item in issues]) from error
    except ReferenceConflict as error:
        raise HTTPException(409, str(error)) from error
    if row is None:
        raise HTTPException(404, "远程 Agent 连接不存在")
    return response(row, await store.agents())


@router.post("", status_code=201)
async def create_connection(ctx: AppContextDep, body: dict, user: RequestUserDep):
    return await save_connection(ctx, body, user)


@router.put("/{connection_id}")
async def update_connection(ctx: AppContextDep, connection_id: str, body: dict, user: RequestUserDep):
    return await save_connection(ctx, body, user, connection_id)


@router.delete("/{connection_id}", status_code=204)
async def delete_connection(ctx: AppContextDep, connection_id: str, revision: int = Query(ge=1)):
    try:
        if not await ctx.get_remote_agents().connections.delete(connection_id, revision):
            raise HTTPException(404, "远程 Agent 连接不存在")
    except ReferenceConflict as error:
        raise HTTPException(409, str(error)) from error


class ConnectionCheck(BaseModel):
    approve_interface: bool = False
    expected_revision: int | None = None
    expected_interface: dict | None = None


def origin(url: str) -> tuple:
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


@router.post("/{connection_id}/check")
async def check_connection(ctx: AppContextDep, connection_id: str, body: ConnectionCheck | None = None):
    runtime = ctx.get_remote_agents()
    row = await required(runtime, connection_id)
    if body and body.approve_interface and (body.expected_revision != row.revision or not body.expected_interface):
        raise HTTPException(409, "接口候选已变更，请重新检查")
    interface = None
    try:
        async with asyncio.timeout(30):
            inspection = await (await runtime.adapter(row)).inspect()
        inspection = {**inspection, "status": "ok"}
        proposed = inspection.get("approved_interface")
        if proposed and row.config.get("protocol") == "a2a":
            previous = row.config.get("approved_interface")
            if body and body.approve_interface and body.expected_interface != proposed:
                raise HTTPException(409, "Agent Card 声明已变更，请重新检查")
            if proposed == previous or (body and body.approve_interface) or (
                previous is None and origin(row.config["card_url"]) == origin(proposed["url"])
            ):
                interface = proposed
            else:
                inspection = {**inspection, "status": "approval_required", "proposed_interface": proposed}
    except (RemoteAgentError, TimeoutError) as error:
        inspection = {"status": "failed", "message": str(error) if isinstance(error, RemoteAgentError) else "连接检查超时"}
    try:
        updated = await runtime.connections.inspection(row, inspection, interface)
    except ReferenceConflict as error:
        raise HTTPException(409, str(error)) from error
    if updated is None:
        raise HTTPException(404, "远程 Agent 连接不存在")
    return response(updated, await runtime.connections.agents())


class DebugRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(default="", max_length=128 * 1024)
    file_ids: list[str] = Field(default_factory=list, max_length=20)
    attachments: list[AttachmentRef] = Field(default_factory=list, max_length=20)
    invocation_id: str | None = Field(default=None, max_length=64)
    question_id: str | None = Field(default=None, min_length=1, max_length=512)
    action: Literal["send", "refresh", "cancel", "clear"] = "send"


@router.post("/{connection_id}/debug")
async def debug_connection(ctx: AppContextDep, connection_id: str, body: DebugRequest, request: Request, user: RequestUserDep):
    runtime = ctx.get_remote_agents()
    connection = await required(runtime, connection_id)
    files = body.attachments or [AttachmentRef(id=key) for key in body.file_ids]
    current = replace(scope(request, user, ctx), attachments=files, remote_question_id=body.question_id)
    entry = runtime.debug_entries.get(body.invocation_id) if body.invocation_id else None
    if body.invocation_id and (entry is None or entry.connection_id != connection_id):
        raise HTTPException(404, "调试任务不存在")
    try:
        if entry:
            await runtime.authorize(entry, current)
            if entry.id in runtime.debug_actions:
                if body.action == "refresh":
                    return entry.summary()
                raise HTTPException(409, "调试任务正在更新，请稍后再试")
        if body.action == "clear":
            if entry:
                if entry.id in runtime.operations or entry.state.status in {"submitted", "working"}:
                    raise HTTPException(409, "调试任务正在执行，请先取消任务")
                runtime.debug_entries.pop(entry.id, None)
            return {"status": "cleared"}
        if body.action != "send":
            if entry is None:
                raise HTTPException(404, "调试任务不存在")
            if body.action == "refresh":
                state = entry.state
                if entry.id in runtime.operations or not (state.task_id or state.session_id or state.cursor):
                    return entry.summary()
            operation = runtime.refresh if body.action == "refresh" else runtime.cancel
            runtime.debug_actions.add(entry.id)
            try:
                return (await operation(entry, current)).summary()
            finally:
                runtime.debug_actions.discard(entry.id)
        message = body.message.strip() or ("请使用本轮补充的附件。" if files else "")
        if not message:
            raise HTTPException(422, "请输入调试消息")
        if len(runtime.operations) >= 32:
            raise HTTPException(429, "正在运行的调试任务已满，请稍后再试")
        mode = "start"
        if entry is not None:
            if entry.id in runtime.operations:
                raise HTTPException(409, "调试任务正在执行")
            mode = "answer" if entry.state.status == "input_required" else "resume"
            if body.question_id and (mode != "answer" or body.question_id != entry.state.question_id):
                raise HTTPException(409, "远程补问已更新，请刷新后重新选择问题")
        else:
            if body.question_id:
                raise HTTPException(409, "调试任务不存在")
            entry = runtime.create(connection, current)
            runtime.debug_entries[entry.id] = entry
        task = asyncio.create_task(runtime.execute_debug(entry, current, message, mode))
        runtime.operations[entry.id] = task
        task.add_done_callback(lambda finished: (runtime.operations.pop(entry.id, None),
                                                 finished.exception() if not finished.cancelled() else None))
        await asyncio.wait({task}, timeout=0.35)
        return (task.result() if task.done() else entry).summary()
    except RemoteAgentError as error:
        raise HTTPException(409, str(error)) from error
