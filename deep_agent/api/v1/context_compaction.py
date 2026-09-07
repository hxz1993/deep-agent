from __future__ import annotations

import json
from contextlib import aclosing

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.headers import extract_forward_headers
from deep_agent.agent.history import has_unprocessed_tool_calls
from deep_agent.agent.run_identity import conversation_id_for, new_run_id
from deep_agent.api.v1.deps import build_runner_for_agent
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.core.context import AppContextDep
from deep_agent.core.exceptions import AgentNotFoundError, LlmModelNotFoundError
from deep_agent.db.ids import normalize_agent_id
from deep_agent.db.session_scope import resolve_session

router = APIRouter()


class ManualContextCompactionRequest(BaseModel):
    model_id: str | None = None


@router.post("/sessions/{session_id}/compact")
async def compact_session_context(
    request: Request,
    ctx: AppContextDep,
    user_id: RequestUserDep,
    session_id: str,
    body: ManualContextCompactionRequest,
    agent_id: str | None = None,
) -> EventSourceResponse:
    normalized_agent_id: str | None = None
    if agent_id is not None:
        try:
            normalized_agent_id = normalize_agent_id(agent_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

    resolved = await resolve_session(
        ctx.store,
        session_id,
        agent_id=normalized_agent_id,
        user_id=user_id,
    )
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    scope, resolved_session_id = resolved
    api_agent_id = normalized_agent_id or scope

    request_headers = extract_forward_headers(request)
    try:
        runner = await build_runner_for_agent(
            ctx,
            api_agent_id,
            model_id=body.model_id,
            request_headers=request_headers,
        )
    except (AgentNotFoundError, LlmModelNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if not runner.manual_compaction_enabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "context_window_unknown",
                "message": "当前模型未配置上下文窗口，无法主动压缩。",
            },
        )
    history = await ctx.store.get_history(
        resolved_session_id,
        agent_id=api_agent_id,
    )
    if has_unprocessed_tool_calls(history):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "pending_deferred",
                "message": "当前会话仍在等待工具交互，请先完成或取消。",
            },
        )

    deps = AgentDeps(
        session_id=resolved_session_id,
        user=user_id,
        agent_id=api_agent_id,
        conversation_id=conversation_id_for(api_agent_id, resolved_session_id),
        run_id=new_run_id(),
        request_headers=request_headers,
        file_store=ctx.file_store,
        app_id=request_headers.get("app"),
        space_id=request_headers.get("space"),
    )

    async def event_generator():
        async with aclosing(runner.run_manual_compaction(resolved_session_id, deps)) as stream:
            async for event in stream:
                yield {
                    "event": event["type"],
                    "data": json.dumps(event, ensure_ascii=False, default=str),
                }

    return EventSourceResponse(event_generator())
