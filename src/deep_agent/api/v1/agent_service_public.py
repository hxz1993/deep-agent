"""Unreleased HTTP task API for the CLI and SDK drafts."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from deep_agent.agent_service.public import (
    HistoryLength, PublicTasks, ReplyInput, SubmitInput, TaskId, TaskQuery, TaskState, task_error,
)
from deep_agent.agent_service.publications import AgentCode
from deep_agent.agent_service.security import platform_caller
from deep_agent.agent_service.service import bounded_body
from deep_agent.core.context import AppContextDep

# Tests can mount the draft explicitly without registering it in the product.
draft_router = APIRouter(prefix="/agent-service")


async def body_as(request: Request, model):
    try:
        return model.model_validate_json(await bounded_body(request))
    except (ValidationError, RecursionError) as exc:
        raise HTTPException(422, "请求 JSON 或消息字段无效") from exc


async def respond(operation, *, status_code=200):
    try:
        result = await operation
    except Exception as exc:
        error = task_error(exc)
        if error is exc:
            raise
        raise error from exc
    return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})


@draft_router.get("/agents")
async def agents(ctx: AppContextDep, request: Request):
    headers = await platform_caller(request, require_app=False)
    return await respond(PublicTasks(ctx, headers, "http").list_agents())


def client_label(request: Request) -> str | None:
    value = request.headers.get("X-DeepAgent-Client")
    return value if value in {"cli", "python-sdk"} else None


@draft_router.post("/agents/{agent_code}/tasks", status_code=202)
async def submit(ctx: AppContextDep, request: Request, agent_code: AgentCode):
    headers = await platform_caller(request)
    message = await body_as(request, SubmitInput)
    tasks = PublicTasks(ctx, headers, "http", client=client_label(request))
    return await respond(tasks.submit(agent_code, message), status_code=202)


@draft_router.get("/agents/{agent_code}/tasks")
async def recent(
    ctx: AppContextDep, request: Request, agent_code: AgentCode,
    context_id: TaskId | None = None, state: TaskState | None = None,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(min_length=1, max_length=4096)] = None,
):
    headers = await platform_caller(request)
    query = TaskQuery(context_id=context_id, state=state, page_size=page_size, cursor=cursor)
    return await respond(PublicTasks(ctx, headers, "http").list_tasks(agent_code, query))


@draft_router.get("/tasks/{task_id}")
async def get(ctx: AppContextDep, request: Request, task_id: TaskId, history_length: HistoryLength | None = None):
    headers = await platform_caller(request)
    return await respond(PublicTasks(ctx, headers, "http").get(task_id, history_length))


@draft_router.post("/tasks/{task_id}/messages", status_code=202)
async def reply(ctx: AppContextDep, request: Request, task_id: TaskId):
    headers = await platform_caller(request)
    message = await body_as(request, ReplyInput)
    return await respond(PublicTasks(ctx, headers, "http").reply(task_id, message), status_code=202)


@draft_router.post("/tasks/{task_id}/cancel")
async def cancel(ctx: AppContextDep, request: Request, task_id: TaskId):
    headers = await platform_caller(request)
    return await respond(PublicTasks(ctx, headers, "http").cancel(task_id))
