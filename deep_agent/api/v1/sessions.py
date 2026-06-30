from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic_ai.messages import ModelMessage

from deep_agent.core.context import AppContextDep
from deep_agent.db.session_list import MAX_LIST_LIMIT, SessionListFilters, SessionSummaryRow
from deep_agent.db.session_scope import resolve_session
from deep_agent.agent.history import (
    SessionHistoryResponse,
    enrich_history_messages,
    first_user_prompt,
    model_messages_to_history,
    session_activity_timestamps,
)
from deep_agent.agent.spec import AgentSpec, anonymous_spec
from deep_agent.capabilities.mcp.resolver import load_mcp_rows
from deep_agent.capabilities.ui.registry import load_ui_tool_hints
from deep_agent.schemas.chat import SessionInfo, SessionListResponse

router = APIRouter()


async def _resolve_agent_name(ctx: AppContextDep, agent_id: str | None) -> str | None:
    if agent_id is None:
        return None
    row = await ctx.stores.agent.get_by_id(agent_id)
    return row.name if row else None


def _summary_to_session_info(row: SessionSummaryRow) -> SessionInfo:
    return SessionInfo(
        session_id=row.session_id,
        message_count=row.message_count,
        agent_id=row.agent_id,
        agent_name=row.agent_name,
        user_prompt=row.user_prompt,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _build_session_info(
    ctx: AppContextDep,
    session_id: str,
    *,
    agent_id: str | None,
    history: list[ModelMessage] | None = None,
) -> SessionInfo:
    if history is None:
        history = await ctx.store.get_history(session_id, agent_id=agent_id)
    agent_name = await _resolve_agent_name(ctx, agent_id)

    created_at: datetime | None = None
    updated_at: datetime | None = None
    if ctx.stores.chat_session is not None:
        from deep_agent.db.session_scope import normalize_agent_scope

        created_at, updated_at = await ctx.stores.chat_session.get_timestamps(
            normalize_agent_scope(agent_id),
            session_id,
        )
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
) -> SessionInfo:
    resolved = await resolve_session(ctx.store, session_id, agent_id=agent_id)
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    scope, resolved_session_id = resolved
    from deep_agent.db.session_scope import agent_scope_to_api

    api_agent_id = agent_id or agent_scope_to_api(scope)
    history = await ctx.store.get_history(resolved_session_id, agent_id=api_agent_id)
    return await _build_session_info(
        ctx,
        resolved_session_id,
        agent_id=api_agent_id,
        history=history,
    )


async def _spec_for_history(ctx: AppContextDep, agent_id: str | None) -> AgentSpec:
    if agent_id:
        row = await ctx.stores.agent.get_by_id(agent_id)
        if row is not None:
            return AgentSpec.from_row(row, row.llm_model)
    return anonymous_spec()


@router.get("/sessions/{session_id}/history", response_model=SessionHistoryResponse)
async def get_session_history(
    ctx: AppContextDep,
    session_id: str,
    agent_id: str | None = None,
) -> SessionHistoryResponse:
    resolved = await resolve_session(ctx.store, session_id, agent_id=agent_id)
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    scope, resolved_session_id = resolved
    from deep_agent.db.session_scope import agent_scope_to_api

    api_agent_id = agent_id or agent_scope_to_api(scope)
    raw = await ctx.store.get_history(resolved_session_id, agent_id=api_agent_id)
    messages = model_messages_to_history(raw)
    mcp_rows = await load_mcp_rows(ctx.stores)
    spec = await _spec_for_history(ctx, api_agent_id)
    hints = await load_ui_tool_hints(ctx.stores, spec, mcp_rows=mcp_rows)
    messages = enrich_history_messages(messages, hints)
    return SessionHistoryResponse(
        session_id=resolved_session_id,
        messages=messages,
        model_message_count=len(raw),
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_200_OK)
async def delete_session(
    ctx: AppContextDep,
    session_id: str,
    agent_id: str | None = None,
) -> None:
    resolved = await resolve_session(ctx.store, session_id, agent_id=agent_id)
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    scope, resolved_session_id = resolved
    from deep_agent.db.session_scope import agent_scope_to_api

    api_agent_id = agent_id or agent_scope_to_api(scope)
    await ctx.store.delete(resolved_session_id, agent_id=api_agent_id)
