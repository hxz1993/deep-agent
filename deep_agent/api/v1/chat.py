from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from sse_starlette.sse import EventSourceResponse

from deep_agent.agent.deferred import build_deferred_tool_results
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.headers import extract_forward_headers
from deep_agent.api.v1.deps import build_runner, build_runner_for_agent
from deep_agent.core.context import AppContextDep
from deep_agent.core.exceptions import AgentNotFoundError
from deep_agent.db.ids import normalize_agent_id
from deep_agent.db.session_scope import runtime_session_key
from deep_agent.schemas.chat import ChatRequest, ChatResponse, StreamChatRequest

router = APIRouter()


def _agent_deps(
    request: Request,
    ctx: AppContextDep,
    *,
    session_id: str,
    user: str | None,
    agent_id: str | None = None,
) -> AgentDeps:
    return AgentDeps(
        session_id=session_id,
        user=user,
        agent_id=agent_id,
        request_headers=extract_forward_headers(request),
    )


def _validate_chat_body(body: ChatRequest | StreamChatRequest) -> None:
    has_message = bool(body.message and body.message.strip())
    deferred = getattr(body, "deferred_tool_results", None)
    has_deferred = bool(deferred)
    if not has_message and not has_deferred:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either message or deferred_tool_results is required.",
        )


def _deferred_results(body: StreamChatRequest):
    if not body.deferred_tool_results:
        return None
    return build_deferred_tool_results(body.deferred_tool_results)


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, ctx: AppContextDep, body: ChatRequest) -> ChatResponse:
    _validate_chat_body(body)
    session_id = body.session_id or uuid.uuid4().hex
    runner = await build_runner(ctx, model=body.model, capabilities=body.capabilities)
    deps = _agent_deps(request, ctx, session_id=session_id, user=body.user)
    result = await runner.run(session_id, body.message or "", deps)
    return ChatResponse(
        session_id=session_id,
        output=result["output"],
        usage=result["usage"],
        status=result.get("status", "completed"),
        deferred=result.get("deferred"),
    )


@router.post("/chat/stream")
async def chat_stream(request: Request, ctx: AppContextDep, body: StreamChatRequest) -> EventSourceResponse:
    _validate_chat_body(body)
    session_id = body.session_id or uuid.uuid4().hex
    deferred_tool_results = _deferred_results(body)
    prompt = body.message or ""

    if body.agent_id is not None:
        try:
            agent_id = normalize_agent_id(body.agent_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        try:
            runner = await build_runner_for_agent(ctx, agent_id)
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        deps = _agent_deps(request, ctx, session_id=session_id, user=body.user, agent_id=agent_id)
        session_data = {"session_id": session_id, "agent_id": agent_id}
    else:
        runner = await build_runner(ctx, model=body.model, capabilities=body.capabilities)
        deps = _agent_deps(request, ctx, session_id=session_id, user=body.user)
        session_data = {"session_id": session_id}

    async def event_generator():
        yield {"event": "session", "data": json.dumps(session_data)}
        async for ev in runner.run_stream(
            session_id,
            prompt,
            deps,
            deferred_tool_results=deferred_tool_results,
        ):
            if await request.is_disconnected():
                break
            yield {"event": ev["type"], "data": json.dumps(ev, ensure_ascii=False)}

    return EventSourceResponse(event_generator())


def mcp_pool_session_key(session_id: str, *, agent_id: str | None = None) -> str:
    """Runtime key for MCP subprocess pooling (not persisted)."""
    return runtime_session_key(session_id, agent_id=agent_id)
