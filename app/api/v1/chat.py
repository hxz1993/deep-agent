from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.agent.deps import AgentDeps
from app.api.v1.deps import build_runner, build_runner_for_agent
from app.core.exceptions import AgentNotFoundError
from app.db.deps import get_db
from app.memory.postgres import scoped_session_key
from app.schemas.chat import ChatRequest, ChatResponse, StreamChatRequest

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    session_id = body.session_id or uuid.uuid4().hex
    runner = build_runner(request, model=body.model, capabilities=body.capabilities)
    deps = AgentDeps(session_id=session_id, user=body.user)
    result = await runner.run(session_id, body.message, deps)
    return ChatResponse(session_id=session_id, output=result["output"], usage=result["usage"])


@router.post("/chat/stream")
async def chat_stream(
    request: Request,
    body: StreamChatRequest,
    db: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    session_id = body.session_id or uuid.uuid4().hex

    if body.agent_id is not None:
        try:
            runner = await build_runner_for_agent(request, db, body.agent_id)
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        run_session_key = scoped_session_key(body.agent_id, session_id)
        deps = AgentDeps(session_id=run_session_key, user=body.user, agent_id=body.agent_id)
        session_data = {"session_id": session_id, "agent_id": str(body.agent_id)}
    else:
        runner = build_runner(request, model=body.model, capabilities=body.capabilities)
        run_session_key = session_id
        deps = AgentDeps(session_id=session_id, user=body.user)
        session_data = {"session_id": session_id}

    async def event_generator():
        yield {"event": "session", "data": json.dumps(session_data)}
        async for ev in runner.run_stream(run_session_key, body.message, deps):
            if await request.is_disconnected():
                break
            yield {"event": ev["type"], "data": json.dumps(ev, ensure_ascii=False)}

    return EventSourceResponse(event_generator())
