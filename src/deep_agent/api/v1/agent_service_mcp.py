"""Environment-level MCP endpoint for published agents."""
from __future__ import annotations

import json
import os
from base64 import b64encode
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Literal
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
from deep_agent.agent_service.mcp_guides import MCP_GUIDE, MCP_GUIDE_URI, MCP_INSTRUCTIONS
from deep_agent.agent_service.public import (
    HistoryLength, PublicInput, PublicTasks, ReplyInput, SubmitInput, TaskId, TaskQuery, artifact_reference, task_error,
)
from deep_agent.agent_service.publications import AgentCode
from deep_agent.agent_service.security import platform_caller, public_base_url
from deep_agent.agent_service.service import bounded_body
from deep_agent.api.v1.agent_service import admin
from deep_agent.core.context import AppContextDep

router = APIRouter(prefix="/agent-service")
tag = "agent-service"
server = Server("deepagent-agents", instructions=MCP_INSTRUCTIONS)
current: ContextVar[PublicTasks] = ContextVar("agent_service_mcp")
INLINE_ARTIFACT_LIMIT = 20 * 1024 * 1024


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
        artifact = await tasks.artifact(task_id, artifact_id)
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
    format: Literal["url", "base64"] = "url"


async def get_artifact(tasks: PublicTasks, args: ArtifactTool) -> dict:
    artifact = await tasks.artifact(args.task_id, args.artifact_id)
    if args.format == "url" or artifact.size > INLINE_ARTIFACT_LIMIT:
        if not tasks.download_base_url:
            raise HTTPException(503, "服务未配置有效的对外下载基址")
        reference = artifact_reference(args.task_id, args.artifact_id, artifact, tasks.download_base_url)
        return {**reference, "delivery": "http_download"}

    metadata = artifact_reference(args.task_id, args.artifact_id, artifact)
    with await tasks.service.open_artifact(artifact, tasks.headers) as handle:
        data = await anyio.to_thread.run_sync(handle.read, artifact.size + 1)
    if len(data) != artifact.size:
        raise HTTPException(502, "文件大小与产物元数据不一致")
    encoded = await anyio.to_thread.run_sync(b64encode, data)
    return {**metadata, "delivery": "inline", "data_base64": encoded.decode("ascii")}


TOOLS: dict[str, tuple[str, type[PublicInput], Callable[..., Awaitable[dict]]]] = {
    "list_agents": ("Discover enabled published Agents and their agent_code values.",
                    PublicInput, lambda tasks, _: tasks.list_agents()),
    "submit_task": ("Start a task with text, data or attachments. Use context_id to continue a completed conversation, "
                    "and reuse the same message_id and payload on retry.",
                    SubmitTool, lambda tasks, a: tasks.submit(a.agent_code, a)),
    "get_task": ("Read task status, output, and file artifacts. Use question_id to reply. Download selected files via "
                 "HTTP GET to download_url using the MCP connection's app and authentication headers.",
                 GetTool, lambda tasks, a: tasks.get(a.task_id, a.history_length)),
    "list_tasks": ("List recent tasks for a published Agent in the configured App. Filter by state or context_id "
                   "and pass next_cursor as cursor to continue.",
                   ListTool, lambda tasks, a: tasks.list_tasks(a.agent_code, a)),
    "reply_task": ("Answer a task in input_required, using its task_id, question_id as reply_to, "
                   "and a new message_id. Attachments can accompany the answer.",
                   ReplyTool, lambda tasks, a: tasks.reply(a.task_id, a)),
    "cancel_task": ("Request cancellation of an unfinished task by task_id and read the returned state.",
                    TaskTool, lambda tasks, a: tasks.cancel(a.task_id)),
    "get_artifact": ("Get a file's complete download_url by task_id and artifact_id (format=url by default). "
                     "Download with the connection's app and authentication headers. If your client cannot send those "
                     "headers, request format=base64 for a file up to 20 MiB. Decode data_base64 from the original "
                     "text JSON and save the bytes under name. Larger files always return the download URL.",
                     ArtifactTool, get_artifact),
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
    content = types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))
    if name == "get_artifact":
        # Keep file bytes only in the text JSON so clients receive a single payload.
        result.pop("data_base64", None)
    return types.CallToolResult(content=[content], structuredContent=result)


def tool_error(status_code: int, detail: str):
    return types.CallToolResult(
        isError=True, content=[types.TextContent(type="text", text=str(detail))],
        structuredContent={"status": status_code, "detail": detail},
    )


@server.list_resources()
async def list_resources():
    return [types.Resource(
        uri=MCP_GUIDE_URI, name="发布 Agent MCP 接入指南",
        description="工具示例、任务续跑、附件与产物", mimeType="text/markdown",
    )]


@server.read_resource()
async def read_resource(uri):
    if str(uri) == MCP_GUIDE_URI:
        return [ReadResourceContents(MCP_GUIDE, "text/markdown")]
    raise McpError(types.ErrorData(code=-32002, message="未知资源", data={"status": 404}))


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
    base = mcp_base_url()
    if base:
        public = urlsplit(base)
        allowed_origins.add(f"{public.scheme}://{public.netloc}")
    if request.url.hostname in {"localhost", "127.0.0.1", "::1"}:
        allowed_origins.add(f"{request.url.scheme}://{request.url.netloc}")
    if origin and origin not in allowed_origins:
        raise HTTPException(403, "MCP Origin 不受信任")
    headers = await platform_caller(request, require_app=False)
    body = await bounded_body(request) if request.method == "POST" else b""
    return MCPResponse(PublicTasks(ctx, headers, "mcp", download_base_url=base), body)
