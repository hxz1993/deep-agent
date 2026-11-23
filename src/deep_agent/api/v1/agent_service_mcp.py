"""Environment-level MCP endpoint for published agents."""
from __future__ import annotations

import json
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

from deep_agent.agent_service.download import ArtifactDownloadResponse
from deep_agent.agent_service.mcp_guides import (
    MCP_FILE_DELIVERY_HINT,
    MCP_GUIDE,
    MCP_GUIDE_NAME,
    MCP_GUIDE_URI,
    MCP_INSTRUCTIONS,
)
from deep_agent.agent_service.mcp_tasks import CancelTool, GetTool, ListTool, McpTasks, SendTool
from deep_agent.agent_service.public import PublicInput, TaskId, task_error
from deep_agent.agent_service.security import needs_platform_address, observe_access, platform_caller, service_base_url
from deep_agent.agent_service.service import bounded_body
from deep_agent.api.v1.agent_service import admin
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.core.context import AppContextDep

router = APIRouter(prefix="/agent-service")
tag = "agent-service"
server = Server("deepagent-agents", instructions=MCP_INSTRUCTIONS)
current: ContextVar[McpTasks] = ContextVar("agent_service_mcp")


@router.get("/client-config", dependencies=admin)
async def client_config(request: Request, ctx: AppContextDep, user: RequestUserDep):
    base = await ctx.agent_service_address.for_admin(request, user)
    return JSONResponse({"mcp_url": f"{base}/api/v1/agent-service/mcp" if base else None},
                        headers={"Cache-Control": "no-store"})


@router.get("/tasks/{task_id}/artifacts/{artifact_id}/download")
async def download(ctx: AppContextDep, request: Request, task_id: TaskId, artifact_id: TaskId):
    with observe_access("mcp"):
        headers = await platform_caller(request)
    tasks = McpTasks(ctx, headers)
    try:
        entry, artifact = await tasks.artifact(task_id, artifact_id)
    except Exception as exc:
        error = task_error(exc)
        if error is exc:
            raise
        raise error from exc
    handle = await tasks.service.open_artifact(entry, artifact, headers, protocol="mcp")
    return ArtifactDownloadResponse(entry, artifact, handle, protocol="mcp")


TOOLS: dict[str, tuple[str, type[PublicInput], Callable[..., Awaitable[dict]]]] = {
    "list_agents": ("Discover enabled published Agents and their agent_code values.",
                    PublicInput, lambda tasks, _: tasks.list_agents()),
    "send_message": ("Start a task with agentCode and an A2A message. To continue an input-required task, "
                     "send a new message containing the original taskId and a new messageId. "
                     "Use contextId alone to start a new task in the same conversation. Waits up to "
                     "configuration.waitSeconds (default 120, 0 for immediate return). If still running when "
                     "the wait expires, returns the current task with its original id and contextId. "
                     f"{MCP_FILE_DELIVERY_HINT}",
                     SendTool, lambda tasks, a: tasks.send(a)),
    "get_task": ("Read an A2A Task by id. Waits up to waitSeconds (default 120, 0 for immediate return). "
                 "Check status.state, status.message for questions, and artifacts for results. "
                 f"{MCP_FILE_DELIVERY_HINT}",
                 GetTool, lambda tasks, a: tasks.get_task(a)),
    "list_tasks": ("List tasks for agentCode in this App with A2A filters and paging. "
                   "Use nextPageToken as pageToken and includeArtifacts to include results.",
                   ListTool, lambda tasks, a: tasks.page(a)),
    "cancel_task": ("Cancel a task by id and return its current task state.",
                    CancelTool, lambda tasks, a: tasks.cancel(a.id)),
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
    return types.CallToolResult(content=[content], structuredContent=result)


def tool_error(status_code: int, detail: str):
    return types.CallToolResult(
        isError=True, content=[types.TextContent(type="text", text=str(detail))],
        structuredContent={"status": status_code, "detail": detail},
    )


@server.list_resources()
async def list_resources():
    return [types.Resource(
        uri=MCP_GUIDE_URI, name=MCP_GUIDE_NAME,
        description="工具示例、任务续跑、附件与产物", mimeType="text/markdown",
    )]


@server.read_resource()
async def read_resource(uri):
    if str(uri) == MCP_GUIDE_URI:
        return [ReadResourceContents(MCP_GUIDE, "text/markdown")]
    raise McpError(types.ErrorData(code=-32002, message="未知资源", data={"status": 404}))


class MCPResponse(Response):
    def __init__(self, tasks: McpTasks, body: bytes):
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


def _validate_mcp_origin(origin: str | None, base: str | None) -> None:
    if not origin:
        return
    public = urlsplit(base or "")
    if not base or origin != f"{public.scheme}://{public.netloc}":
        raise HTTPException(403, "MCP Origin 不受信任")


@router.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
async def mcp(request: Request, ctx: AppContextDep):
    origin = request.headers.get("origin")
    base = service_base_url(request)
    use_platform_address = needs_platform_address(request, base)
    with observe_access("mcp"):
        if not use_platform_address:
            _validate_mcp_origin(origin, base)
        headers = await platform_caller(request, require_app=False)
        if use_platform_address:
            base = await ctx.agent_service_address.resolve(request, base, headers)
            _validate_mcp_origin(origin, base)
    body = await bounded_body(request) if request.method == "POST" else b""
    return MCPResponse(McpTasks(ctx, headers, download_base_url=base), body)
