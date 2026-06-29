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
from deep_agent.core.exceptions import AgentNotFoundError, LlmModelNotFoundError
from deep_agent.db.ids import normalize_agent_id
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
    if body.branch_from is not None and has_deferred:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="branch_from and deferred_tool_results are mutually exclusive.",
        )
    if body.branch_from is not None and body.branch_from < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="branch_from must be non-negative.",
        )


async def _validate_branch_from(
    ctx: AppContextDep,
    session_id: str,
    *,
    agent_id: str | None,
    branch_from: int | None,
) -> None:
    if branch_from is None:
        return
    history = await ctx.store.get_history(session_id, agent_id=agent_id)
    if branch_from > len(history):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"branch_from {branch_from} out of range (history length {len(history)}).",
        )


def _deferred_results(body: StreamChatRequest):
    if not body.deferred_tool_results:
        return None
    return build_deferred_tool_results(body.deferred_tool_results)


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, ctx: AppContextDep, body: ChatRequest) -> ChatResponse:
    _validate_chat_body(body)
    session_id = body.session_id or uuid.uuid4().hex
    await _validate_branch_from(ctx, session_id, agent_id=None, branch_from=body.branch_from)
    try:
        runner = await build_runner(
            ctx,
            model=body.model if body.model_id is None else None,
            model_id=body.model_id,
            temperature=body.temperature,
            capabilities=body.capabilities,
        )
    except LlmModelNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    deps = _agent_deps(request, ctx, session_id=session_id, user=body.user)
    result = await runner.run(
        session_id,
        body.message or "",
        deps,
        branch_from=body.branch_from,
    )
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

    api_agent_id: str | None = None
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
        api_agent_id = agent_id
    else:
        try:
            runner = await build_runner(
                ctx,
                model=body.model if body.model_id is None else None,
                model_id=body.model_id,
                temperature=body.temperature,
                capabilities=body.capabilities,
            )
        except LlmModelNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        deps = _agent_deps(request, ctx, session_id=session_id, user=body.user)
        session_data = {"session_id": session_id}
        api_agent_id = None

    await _validate_branch_from(ctx, session_id, agent_id=api_agent_id, branch_from=body.branch_from)

    async def event_generator():
        yield {"event": "session", "data": json.dumps(session_data)}
        async for ev in runner.run_stream(
            session_id,
            prompt,
            deps,
            deferred_tool_results=deferred_tool_results,
            branch_from=body.branch_from,
            should_cancel=request.is_disconnected,
        ):
            yield {"event": ev["type"], "data": json.dumps(ev, ensure_ascii=False)}

    return EventSourceResponse(event_generator())
