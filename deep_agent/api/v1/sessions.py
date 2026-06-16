from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.api.v1.deps import get_store
from deep_agent.db.deps import get_db
from deep_agent.db.repositories.agent import AgentRepository
from deep_agent.memory.base import SessionStore
from deep_agent.memory.history import SessionHistoryResponse, first_user_prompt, model_messages_to_history
from deep_agent.memory.postgres import parse_scoped_session_key
from deep_agent.memory.sql import SqlSessionStore
from deep_agent.schemas.chat import SessionInfo

router = APIRouter()
_agent_repo = AgentRepository()


async def _resolve_agent_name(db: AsyncSession, agent_id: UUID | None) -> str | None:
    if agent_id is None:
        return None
    row = await _agent_repo.get_by_id(db, agent_id)
    return row.name if row else None


async def _build_session_info(
    store: SessionStore,
    session_key: str,
    *,
    db: AsyncSession | None = None,
) -> SessionInfo:
    history = await store.get_history(session_key)
    parsed_agent_id, session_id = parse_scoped_session_key(session_key)

    agent_id = parsed_agent_id
    if isinstance(store, SqlSessionStore):
        stored_agent_id = await store.get_session_agent_id(session_key)
        if stored_agent_id:
            agent_id = stored_agent_id

    agent_name = await _resolve_agent_name(db, agent_id) if db is not None else None

    return SessionInfo(
        session_id=session_id,
        message_count=len(history),
        agent_id=agent_id,
        agent_name=agent_name,
        user_prompt=first_user_prompt(history),
    )


@router.get("/sessions", response_model=list[SessionInfo])
async def list_sessions(request: Request, db: AsyncSession = Depends(get_db)) -> list[SessionInfo]:
    store = get_store(request)
    result: list[SessionInfo] = []
    for session_key in await store.list_sessions():
        result.append(await _build_session_info(store, session_key, db=db))
    return result


@router.get("/sessions/{session_id}", response_model=SessionInfo)
async def get_session(
    request: Request,
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> SessionInfo:
    store = get_store(request)
    history = await store.get_history(session_id)
    if not history and session_id not in await store.list_sessions():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return await _build_session_info(store, session_id, db=db)


@router.get("/sessions/{session_id}/history", response_model=SessionHistoryResponse)
async def get_session_history(request: Request, session_id: str) -> SessionHistoryResponse:
    store = get_store(request)
    raw = await store.get_history(session_id)
    _, display_id = parse_scoped_session_key(session_id)
    return SessionHistoryResponse(session_id=display_id, messages=model_messages_to_history(raw))


@router.delete("/sessions/{session_id}", status_code=status.HTTP_200_OK)
async def delete_session(request: Request, session_id: str) -> None:
    store = get_store(request)
    await store.delete(session_id)
    pool = getattr(request.app.state, "mcp_session_pool", None)
    if pool is not None:
        await pool.close_session(session_id)


