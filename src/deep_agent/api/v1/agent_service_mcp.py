"""Environment-level MCP endpoint for published agents."""
from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from urllib.parse import urlsplit

import anyio
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from mcp import types
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.streamable_http import StreamableHTTPServerTransport
from mcp.shared.exceptions import McpError
from pydantic import ValidationError

from deep_agent.agent_service.download import artifact_download
from deep_agent.agent_service.public import (
    HistoryLength, PublicInput, PublicTasks, ReplyInput, SubmitInput, TaskId, TaskQuery, task_error,
)
from deep_agent.agent_service.publications import AgentCode
from deep_agent.agent_service.security import platform_caller, public_base_url
from deep_agent.agent_service.service import bounded_body
from deep_agent.api.v1.agent_service import admin
from deep_agent.core.context import AppContextDep

router = APIRouter(prefix="/agent-service")
tag = "agent-service"
server = Server(
    "deepagent-agents",
    instructions="Use list_agents to discover enabled agents. Keep the task ID for polling or reply, "
                 "and reuse the same message_id when retrying a submission.",
)
current: ContextVar[PublicTasks] = ContextVar("agent_service_mcp")
RESOURCE_LIMIT = 1024 * 1024


def mcp_base_url() -> str | None:
    try:
        return public_base_url(os.getenv("DEEP_AGENT_AGENT_SERVICE_PUBLIC_BASE_URL", ""))
    except ValueError:
        return None


@router.get("/client-config", dependencies=admin)
async def client_config():
    base = mcp_base_url()
    return JSONResponse({"mcp_url": f"{base}/api/v1/agent-service/mcp" if base else None},
                        headers={"Cache-Control": "no-store"})


@router.get("/tasks/{task_id}/artifacts/{artifact_id}/download")
async def download(ctx: AppContextDep, request: Request, task_id: TaskId, artifact_id: TaskId):
    headers = await platform_caller(request)
    tasks = PublicTasks(ctx, headers, "mcp")
    try:
        _, artifact = await tasks.artifact(task_id, artifact_id)
    except Exception as exc:
        error = task_error(exc)
        if error is exc:
            raise
        raise error from exc
    return await artifact_download(tasks.service, artifact, headers)


class SubmitTool(SubmitInput):
    agent_code: AgentCode


class ListTool(TaskQuery):
    agent_code: AgentCode


class TaskTool(PublicInput):
    task_id: TaskId


class GetTool(TaskTool):
    history_length: HistoryLength | None = None


class ReplyTool(ReplyInput):
    task_id: TaskId


class ArtifactTool(TaskTool):
    artifact_id: TaskId


async def artifact_tool(tasks: PublicTasks, args: ArtifactTool) -> dict:
    result, _ = await tasks.artifact(args.task_id, args.artifact_id)
    return result


TOOLS: dict[str, tuple[str, type[PublicInput], Callable[..., Awaitable[dict]]]] = {
    "list_agents": ("List enabled Agent publications.", PublicInput, lambda tasks, _: tasks.list_agents()),
    "submit_task": ("Send a message to a published Agent. Supply a stable message_id for safe retries.",
                    SubmitTool, lambda tasks, a: tasks.submit(a.agent_code, a)),
    "get_task": ("Read task status, results, and artifact references.",
                 GetTool, lambda tasks, a: tasks.get(a.task_id, a.history_length)),
    "list_tasks": ("List recent tasks started for this Agent using the configured App.",
                   ListTool, lambda tasks, a: tasks.list_tasks(a.agent_code, a)),
    "reply_task": ("Supply requested input to an existing task. Use the returned question_id as reply_to.",
                   ReplyTool, lambda tasks, a: tasks.reply(a.task_id, a)),
    "cancel_task": ("Cancel a task.", TaskTool, lambda tasks, a: tasks.cancel(a.task_id)),
    "get_artifact": ("Get an authorized file artifact reference.", ArtifactTool, artifact_tool),
}


@server.list_tools()
async def list_tools():
    return [
        types.Tool(name=name, description=description, inputSchema=model.model_json_schema())
        for name, (description, model, _) in TOOLS.items()
    ]


@server.call_tool(validate_input=False)
async def call_tool(name: str, arguments: dict):
    tool = TOOLS.get(name)
    if tool is None:
        return tool_error(404, "未知工具")
    _, model, operation = tool
    try:
        params = model.model_validate(arguments)
    except ValidationError:
        return tool_error(422, "工具参数无效")
    try:
        tasks = current.get()
        if name != "list_agents" and not tasks.headers.get("app"):
            raise HTTPException(400, "请在 MCP 连接的 app Header 中传入 App ID")
        result = await operation(tasks, params)
    except Exception as exc:
        error = task_error(exc)
        return tool_error(error.status_code, error.detail)
    if name == "get_artifact":
        content = [types.ResourceLink(
            type="resource_link", name=result["name"], uri=result["resource_uri"],
            mimeType=result["media_type"], size=result["size"],
        )]
    else:
        # Include the actual result for hosts that only read the text channel.
        content = [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
    return types.CallToolResult(content=content, structuredContent=result)


def tool_error(status_code: int, detail: str):
    return types.CallToolResult(
        isError=True, content=[types.TextContent(type="text", text=str(detail))],
        structuredContent={"status": status_code, "detail": detail},
    )


@server.list_resources()
async def list_resources():
    # References are tied to authorized tasks and are issued by get_artifact.
    return []


@server.read_resource()
async def read_resource(uri):
    match = re.fullmatch(r"deepagent://tasks/([a-f0-9]{32})/artifacts/([a-f0-9]{32})", str(uri))
    try:
        if not match:
            raise HTTPException(404, "未知产物引用")
        tasks = current.get()
        _, artifact = await tasks.artifact(*match.groups())
        if artifact.size > RESOURCE_LIMIT:
            raise HTTPException(413, "请使用鉴权下载接口获取完整文件")
        handle = await tasks.service.open_artifact(artifact, tasks.headers)
        try:
            content = await asyncio.to_thread(handle.read, RESOURCE_LIMIT + 1)
        finally:
            handle.close()
        if len(content) != artifact.size:
            raise HTTPException(502, "文件大小与产物元数据不一致")
        return [ReadResourceContents(content, artifact.mime)]
    except Exception as exc:
        error = task_error(exc)
        raise McpError(types.ErrorData(code=-32002, message=str(error.detail), data={"status": error.status_code})) from exc


class MCPResponse(Response):
    def __init__(self, tasks: PublicTasks, body: bytes):
        super().__init__()
        self.tasks = tasks
        self.request_body = body

    async def __call__(self, scope, receive, send):
        transport = StreamableHTTPServerTransport(None, is_json_response_enabled=True)
        pending = True

        async def receive_body():
            nonlocal pending
            if pending:
                pending = False
                return {"type": "http.request", "body": self.request_body, "more_body": False}
            return await receive()

        async def no_store(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), (b"cache-control", b"no-store")]
            await send(message)

        token = current.set(self.tasks)
        try:
            async with transport.connect() as (reader, writer):
                async with anyio.create_task_group() as group:
                    group.start_soon(server.run, reader, writer, server.create_initialization_options(), False, True)
                    try:
                        await transport.handle_request(scope, receive_body, no_store)
                    finally:
                        group.cancel_scope.cancel()
        finally:
            current.reset(token)


@router.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
async def mcp(request: Request, ctx: AppContextDep):
    origin = request.headers.get("origin")
    allowed_origins = set()
    if base := mcp_base_url():
        public = urlsplit(base)
        allowed_origins.add(f"{public.scheme}://{public.netloc}")
    if request.url.hostname in {"localhost", "127.0.0.1", "::1"}:
        allowed_origins.add(f"{request.url.scheme}://{request.url.netloc}")
    if origin and origin not in allowed_origins:
        raise HTTPException(403, "MCP Origin 不受信任")
    headers = await platform_caller(request, require_app=False)
    body = await bounded_body(request) if request.method == "POST" else b""
    return MCPResponse(PublicTasks(ctx, headers, "mcp"), body)
