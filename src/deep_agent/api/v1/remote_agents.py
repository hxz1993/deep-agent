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
from deep_agent.remote_agents.contracts import ConnectionWrite, McpConfig, RemoteAgentError
from deep_agent.remote_agents.runtime import RequestScope
from deep_agent.schemas.attachment import AttachmentRef

router = APIRouter(prefix="/remote-agent-connections", dependencies=CONSOLE_ADMIN_DEPENDENCIES)
tag = "remote-agents"


def response(row, agents: dict) -> dict:
    config = dict(row.config)
    if config.get("protocol") == "a2a":
        config["headers"] = dict.fromkeys(config.get("headers", {}))
    return {
        **{key: getattr(row, key) for key in ("id", "name", "description", "revision", "inspection", "created_at", "updated_at")},
        "config": config, "agents": agents.get(row.id, []),
    }


async def required(runtime, connection_id):
    row = await runtime.connections.get(connection_id)
    if row is None:
        raise HTTPException(404, "远程 Agent 连接不存在")
    return row


def scope(request: Request, user: str, ctx: AppContextDep) -> RequestScope:
    headers = extract_execution_context_headers(request)
    headers["user"] = user
    return RequestScope(user, ctx.file_store, headers, app_id=headers.get("app"), space_id=headers.get("space"))


@router.get("")
async def list_connections(ctx: AppContextDep):
    store = ctx.get_remote_agents().connections
    agents = await store.agents()
    return [response(row, agents) for row in await store.list()]


@router.get("/recent")
async def recent_connections(ctx: AppContextDep, request: Request, user: RequestUserDep):
    runtime = ctx.get_remote_agents()
    current = scope(request, user, ctx)
    entries = runtime.recent.list(current)
    links = await runtime.connections.agents()
    return [entry for entry in entries if any(item["id"] == entry["agent_id"]
            for item in links.get(entry["connection_id"], ()))]


class McpPublications(BaseModel):
    mcp_server_name: str = Field(min_length=1, max_length=191)


@router.post("/mcp-publications")
async def mcp_publications(ctx: AppContextDep, body: McpPublications):
    try:
        adapter = await ctx.get_remote_agents().mcp_adapter(McpConfig(
            mcp_server_name=body.mcp_server_name, publication_code="discovery",
        ))
        return await adapter.publications()
    except RemoteAgentError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/{connection_id}")
async def get_connection(ctx: AppContextDep, connection_id: str):
    runtime = ctx.get_remote_agents()
    return response(await required(runtime, connection_id), await runtime.connections.agents())


async def save_connection(ctx, data: dict, connection_id: str | None = None):
    store = ctx.get_remote_agents().connections
    try:
        body = ConnectionWrite.model_validate(data)
        if connection_id and body.revision is None:
            raise HTTPException(422, "更新连接必须携带版本")
        row = await store.save(body, connection_id)
    except ValidationError as error:
        issues = error.errors(include_context=False, include_input=False, include_url=False)
        raise HTTPException(422, [{"loc": item["loc"], "type": item["type"], "msg": item["msg"]} for item in issues]) from error
    except ReferenceConflict as error:
        raise HTTPException(409, str(error)) from error
    if row is None:
        raise HTTPException(404, "远程 Agent 连接不存在")
    return response(row, await store.agents())


@router.post("", status_code=201)
async def create_connection(ctx: AppContextDep, body: dict):
    return await save_connection(ctx, body)


@router.put("/{connection_id}")
async def update_connection(ctx: AppContextDep, connection_id: str, body: dict):
    return await save_connection(ctx, body, connection_id)


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
    action: Literal["send", "refresh", "cancel", "clear"] = "send"


@router.post("/{connection_id}/debug")
async def debug_connection(ctx: AppContextDep, connection_id: str, body: DebugRequest, request: Request, user: RequestUserDep):
    runtime = ctx.get_remote_agents()
    connection = await required(runtime, connection_id)
    files = body.attachments or [AttachmentRef(id=key) for key in body.file_ids]
    current = replace(scope(request, user, ctx), attachments=files)
    entry = runtime.debug_entries.get(body.invocation_id) if body.invocation_id else None
    if body.invocation_id and (entry is None or entry.connection_id != connection_id):
        raise HTTPException(404, "调试任务不存在")
    try:
        if entry:
            await runtime.authorize(entry, current)
        if body.action == "clear":
            if entry and entry.id not in runtime.operations:
                runtime.debug_entries.pop(entry.id, None)
            return {"status": "cleared"}
        if body.action != "send":
            if entry is None:
                raise HTTPException(404, "调试任务不存在")
            if body.action == "refresh" and entry.id in runtime.operations:
                return entry.summary()
            operation = runtime.refresh if body.action == "refresh" else runtime.cancel
            result = await operation(entry, current)
            active = runtime.operations.get(entry.id)
            if result.state.status == "canceled" and active:
                active.cancel()
            return result.summary()
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
        else:
            entry = runtime.create(connection, current)
            runtime.debug_entries[entry.id] = entry
        task = asyncio.create_task(runtime.execute(entry, current, message, mode))
        runtime.operations[entry.id] = task
        task.add_done_callback(lambda finished: (runtime.operations.pop(entry.id, None),
                                                 finished.exception() if not finished.cancelled() else None))
        await asyncio.wait({task}, timeout=0.35)
        return (task.result() if task.done() else entry).summary()
    except RemoteAgentError as error:
        raise HTTPException(409, str(error)) from error
