from __future__ import annotations

import json
import uuid
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from sse_starlette.sse import EventSourceResponse

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.headers import extract_forward_headers
from deep_agent.api.v1.deps import build_runner, build_runner_for_agent, get_settings
from deep_agent.core.exceptions import AgentNotFoundError
from deep_agent.db.deps import db_session
from deep_agent.memory.postgres import scoped_session_key
from deep_agent.schemas.chat import ChatRequest, ChatResponse, StreamChatRequest

router = APIRouter()


def _agent_deps(
    request: Request,
    *,
    session_id: str,
    user: str | None,
    agent_id: UUID | None = None,
) -> AgentDeps:
    settings = get_settings(request)
    return AgentDeps(
        session_id=session_id,
        user=user,
        agent_id=agent_id,
        request_headers=extract_forward_headers(request, settings),
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    session_id = body.session_id or uuid.uuid4().hex
    runner = build_runner(request, model=body.model, capabilities=body.capabilities)
    deps = _agent_deps(request, session_id=session_id, user=body.user)
    result = await runner.run(session_id, body.message, deps)
    return ChatResponse(session_id=session_id, output=result["output"], usage=result["usage"])


@router.post("/chat/stream")
async def chat_stream(request: Request, body: StreamChatRequest) -> EventSourceResponse:
    session_id = body.session_id or uuid.uuid4().hex

    if body.agent_id is not None:
        try:
            async with db_session(request) as db:
                runner = await build_runner_for_agent(request, db, body.agent_id)
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        run_session_key = scoped_session_key(body.agent_id, session_id)
        deps = _agent_deps(request, session_id=run_session_key, user=body.user, agent_id=body.agent_id)
        session_data = {"session_id": session_id, "agent_id": str(body.agent_id)}
    else:
        runner = build_runner(request, model=body.model, capabilities=body.capabilities)
        run_session_key = session_id
        deps = _agent_deps(request, session_id=session_id, user=body.user)
        session_data = {"session_id": session_id}

    async def event_generator():
        yield {"event": "session", "data": json.dumps(session_data)}
        async for ev in runner.run_stream(run_session_key, body.message, deps):
            if await request.is_disconnected():
                break
            yield {"event": ev["type"], "data": json.dumps(ev, ensure_ascii=False)}

    return EventSourceResponse(event_generator())
