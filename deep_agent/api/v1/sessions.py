from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.core.context import AppContextDep
from deep_agent.db.deps import get_db
from deep_agent.db.repositories.agent import AgentRepository
from deep_agent.memory.base import SessionStore
from deep_agent.memory.history import (
    SessionHistoryResponse,
    first_user_prompt,
    model_messages_to_history,
    session_activity_timestamps,
)
from deep_agent.memory.postgres import parse_scoped_session_key, resolve_session_key
from deep_agent.memory.sql import SqlSessionStore
from deep_agent.schemas.chat import SessionInfo

router = APIRouter()
_agent_repo = AgentRepository()


async def _resolve_agent_name(db: AsyncSession, agent_id: str | None) -> str | None:
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

    created_at: datetime | None = None
    updated_at: datetime | None = None
    if isinstance(store, SqlSessionStore):
        created_at, updated_at = await store.get_session_timestamps(session_key)
    if updated_at is None and history:
        created_at, updated_at = session_activity_timestamps(history)

    return SessionInfo(
        session_id=session_id,
        message_count=len(history),
        agent_id=agent_id,
        agent_name=agent_name,
        user_prompt=first_user_prompt(history),
        created_at=created_at,
        updated_at=updated_at,
    )


@router.get("/sessions", response_model=list[SessionInfo])
async def list_sessions(ctx: AppContextDep, db: AsyncSession = Depends(get_db)) -> list[SessionInfo]:
    result: list[SessionInfo] = []
    for session_key in await ctx.store.list_sessions():
        result.append(await _build_session_info(ctx.store, session_key, db=db))
    return result


@router.get("/sessions/{session_id}", response_model=SessionInfo)
async def get_session(
    ctx: AppContextDep,
    session_id: str,
    agent_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> SessionInfo:
    session_key = await resolve_session_key(ctx.store, session_id, agent_id=agent_id)
    if session_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    history = await ctx.store.get_history(session_key)
    if not history and session_key not in await ctx.store.list_sessions():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return await _build_session_info(ctx.store, session_key, db=db)


@router.get("/sessions/{session_id}/history", response_model=SessionHistoryResponse)
async def get_session_history(
    ctx: AppContextDep,
    session_id: str,
    agent_id: str | None = None,
) -> SessionHistoryResponse:
    session_key = await resolve_session_key(ctx.store, session_id, agent_id=agent_id)
    if session_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    raw = await ctx.store.get_history(session_key)
    _, display_id = parse_scoped_session_key(session_key)
    return SessionHistoryResponse(session_id=display_id, messages=model_messages_to_history(raw))


@router.delete("/sessions/{session_id}", status_code=status.HTTP_200_OK)
async def delete_session(
    ctx: AppContextDep,
    session_id: str,
    agent_id: str | None = None,
) -> None:
    session_key = await resolve_session_key(ctx.store, session_id, agent_id=agent_id)
    if session_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    await ctx.store.delete(session_key)
    await ctx.mcp_session_pool.close_session(session_key)

