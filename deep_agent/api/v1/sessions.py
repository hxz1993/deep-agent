from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.core.context import AppContextDep
from deep_agent.db.deps import db_session, get_db
from deep_agent.db.repositories.agent import AgentRepository
from deep_agent.db.repositories.session import MAX_LIST_LIMIT, SessionRepository, SessionSummaryRow
from deep_agent.memory.base import SessionStore
from deep_agent.memory.history import (
    SessionHistoryResponse,
    first_user_prompt,
    model_messages_to_history,
    session_activity_timestamps,
)
from deep_agent.memory.postgres import parse_scoped_session_key, resolve_session_key
from deep_agent.memory.sql import SqlSessionStore
from deep_agent.schemas.chat import SessionInfo, SessionListResponse

router = APIRouter()
_agent_repo = AgentRepository()
_session_repo = SessionRepository()


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


async def _list_sessions_in_memory(
    store: SessionStore,
    *,
    agent_id: str | None,
    limit: int,
    offset: int,
    updated_after: datetime | None,
    updated_before: datetime | None,
    has_messages: bool | None,
    sort: Literal["updated_at", "created_at"],
    db: AsyncSession | None,
) -> SessionListResponse:
    items: list[SessionInfo] = []
    for session_key in await store.list_sessions():
        info = await _build_session_info(store, session_key, db=db)
        if agent_id is not None and info.agent_id != agent_id:
            continue
        if updated_after is not None and (info.updated_at is None or info.updated_at < updated_after):
            continue
        if updated_before is not None and (info.updated_at is None or info.updated_at > updated_before):
            continue
        if has_messages is True and info.message_count == 0:
            continue
        if has_messages is False and info.message_count > 0:
            continue
        items.append(info)

    reverse = True
    if sort == "updated_at":
        items.sort(key=lambda item: item.updated_at or datetime.min, reverse=reverse)
    else:
        items.sort(key=lambda item: item.created_at or datetime.min, reverse=reverse)

    total = len(items)
    page = items[offset : offset + limit]
    return SessionListResponse(items=page, total=total, limit=limit, offset=offset)


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
    if isinstance(ctx.store, SqlSessionStore):
        async with db_session(ctx) as db:
            rows, total = await _session_repo.list_summaries(
                db,
                agent_id=agent_id,
                user_id=user_id,
                limit=limit,
                offset=offset,
                updated_after=updated_after,
                updated_before=updated_before,
                has_messages=has_messages,
                sort=sort,
            )
        return SessionListResponse(
            items=[_summary_to_session_info(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    return await _list_sessions_in_memory(
        ctx.store,
        agent_id=agent_id,
        limit=limit,
        offset=offset,
        updated_after=updated_after,
        updated_before=updated_before,
        has_messages=has_messages,
        sort=sort,
        db=None,
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
