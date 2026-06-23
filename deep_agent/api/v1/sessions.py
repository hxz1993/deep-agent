from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.core.context import AppContextDep
from deep_agent.db.deps import get_db
from deep_agent.db.deps import get_agent_repo
from deep_agent.db.session_list import MAX_LIST_LIMIT, SessionListFilters, SessionSummaryRow
from deep_agent.db.session_key import parse_scoped_session_key, resolve_session_key
from deep_agent.db.stores.base import SessionStore
from deep_agent.agent.history import (
    SessionHistoryResponse,
    first_user_prompt,
    model_messages_to_history,
    session_activity_timestamps,
)
from deep_agent.schemas.chat import SessionInfo, SessionListResponse

router = APIRouter()
_agent_repo = get_agent_repo()


async def _resolve_agent_name(db: AsyncSession, agent_id: str | None) -> str | None:
    if agent_id is None:
        return None
    row = await _agent_repo.get_by_id(db, agent_id)
    return row.name if row else None


def _summary_to_session_info(row: SessionSummaryRow) -> SessionInfo:
    _, session_id = parse_scoped_session_key(row.session_key)
    return SessionInfo(
        session_id=session_id,
        message_count=row.message_count,
        agent_id=row.agent_id,
        agent_name=row.agent_name,
        user_prompt=row.user_prompt,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _build_session_info(
    store: SessionStore,
    session_key: str,
    *,
    db: AsyncSession | None = None,
) -> SessionInfo:
    history = await store.get_history(session_key)
    parsed_agent_id, session_id = parse_scoped_session_key(session_key)

    agent_id = parsed_agent_id
    get_agent_id = getattr(store, "get_session_agent_id", None)
    if get_agent_id is not None:
        stored_agent_id = await get_agent_id(session_key)
        if stored_agent_id:
            agent_id = stored_agent_id

    agent_name = await _resolve_agent_name(db, agent_id) if db is not None else None

    created_at: datetime | None = None
    updated_at: datetime | None = None
    get_timestamps = getattr(store, "get_session_timestamps", None)
    if get_timestamps is not None:
        created_at, updated_at = await get_timestamps(session_key)
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


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    ctx: AppContextDep,
    agent_id: str | None = Query(default=None, description="Filter by agent id."),
    user_id: str | None = Query(default=None, description="Filter by user id."),
    limit: int = Query(default=50, ge=1, le=MAX_LIST_LIMIT, description="Page size."),
    offset: int = Query(default=0, ge=0, description="Page offset."),
    updated_after: datetime | None = Query(default=None, description="Only sessions updated at or after this time (UTC)."),
    updated_before: datetime | None = Query(default=None, description="Only sessions updated at or before this time (UTC)."),
    has_messages: bool | None = Query(
        default=None,
        description="When true, only sessions with messages; when false, only empty sessions.",
    ),
    sort: Literal["updated_at", "created_at"] = Query(
        default="updated_at",
        description="Sort field; results are always descending.",
    ),
) -> SessionListResponse:
    filters = SessionListFilters(
        agent_id=agent_id,
        user_id=user_id,
        limit=limit,
        offset=offset,
        updated_after=updated_after,
        updated_before=updated_before,
        has_messages=has_messages,
        sort=sort,
    )
    rows, total = await ctx.store.list_session_summaries(filters)
    return SessionListResponse(
        items=[_summary_to_session_info(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


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
