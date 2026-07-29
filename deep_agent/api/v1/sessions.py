from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage

from deep_agent.core.context import AppContextDep
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.db.session_list import MAX_LIST_LIMIT, SessionListFilters, SessionSummaryRow
from deep_agent.db.session_scope import resolve_session
from deep_agent.agent.history import (
    SessionHistoryResponse,
    _extract_assistant_text,
    enrich_history_messages,
    first_user_prompt,
    model_messages_to_history,
    session_activity_timestamps,
)
from deep_agent.agent.spec import AgentSpec, anonymous_spec
from deep_agent.agent.context.budget import build_context_budget
from deep_agent.agent.context.estimator import TokenEstimator
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
        title=row.title,
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
    title: str | None = None
    if ctx.stores.chat_session is not None:
        from deep_agent.db.session_scope import normalize_agent_scope

        created_at, updated_at = await ctx.stores.chat_session.get_timestamps(
            normalize_agent_scope(agent_id),
            session_id,
        )
        # 通过 coordinator 获取标题（封装了 db_session 管理）
        title = await ctx.store.get_session_title(session_id, agent_id=agent_id)
    if updated_at is None and history:
        created_at, updated_at = session_activity_timestamps(history)

    return SessionInfo(
        session_id=session_id,
        message_count=len(history),
        agent_id=agent_id,
        agent_name=agent_name,
        user_prompt=first_user_prompt(history),
        title=title,
        created_at=created_at,
        updated_at=updated_at,
    )


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    ctx: AppContextDep,
    user_id: RequestUserDep,
    agent_id: str | None = Query(default=None, description="Filter by agent id."),
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
    user_id: RequestUserDep,
    session_id: str,
    agent_id: str | None = None,
) -> SessionInfo:
    resolved = await resolve_session(
        ctx.store,
        session_id,
        agent_id=agent_id,
        user_id=user_id,
    )
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
    user_id: RequestUserDep,
    session_id: str,
    agent_id: str | None = None,
) -> SessionHistoryResponse:
    resolved = await resolve_session(
        ctx.store,
        session_id,
        agent_id=agent_id,
        user_id=user_id,
    )
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    scope, resolved_session_id = resolved
    from deep_agent.db.session_scope import agent_scope_to_api

    api_agent_id = agent_id or agent_scope_to_api(scope)
    raw = await ctx.store.get_history(resolved_session_id, agent_id=api_agent_id)
    messages = model_messages_to_history(raw)
    mcp_rows = await load_mcp_rows(ctx.stores)
    spec = await _spec_for_history(ctx, api_agent_id)
    hints = await load_ui_tool_hints(ctx.stores, spec)
    messages = enrich_history_messages(messages, hints)
    return SessionHistoryResponse(
        session_id=resolved_session_id,
        messages=messages,
        model_message_count=len(raw),
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_200_OK)
async def delete_session(
    ctx: AppContextDep,
    user_id: RequestUserDep,
    session_id: str,
    agent_id: str | None = None,
) -> None:
    resolved = await resolve_session(
        ctx.store,
        session_id,
        agent_id=agent_id,
        user_id=user_id,
    )
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    scope, resolved_session_id = resolved
    from deep_agent.db.session_scope import agent_scope_to_api

    api_agent_id = agent_id or agent_scope_to_api(scope)
    await ctx.store.delete(resolved_session_id, agent_id=api_agent_id)


class SessionRenameRequest(BaseModel):
    """会话重命名请求体。"""

    title: Optional[str] = None
    """手动设置的标题。与 auto 互斥。"""

    auto: bool = False
    """是否自动重新生成标题（基于对话内容）。与 title 互斥。"""


class SessionContextStatus(BaseModel):
    session_id: str
    raw_message_count: int
    estimated_history_tokens: int
    compaction_enabled: bool
    compaction_reason: str
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    predicted_output_tokens: int | None = None
    output_prediction_source: str | None = None
    output_prediction_sample_count: int = 0
    provider_sample_count: int = 0
    estimated_sample_count: int = 0
    estimator_source: str | None = None
    estimate_scope: str = "history_only"
    trigger_tokens: int | None = None
    target_tokens: int | None = None
    checkpoint_exists: bool = False
    checkpoint_source_last_sequence: int | None = None
    checkpoint_estimated_tokens: int | None = None
    checkpoint_updated_at: datetime | None = None
    summary_model_id: str | None = None


@router.get(
    "/sessions/{session_id}/context-status",
    response_model=SessionContextStatus,
)
async def get_session_context_status(
    ctx: AppContextDep,
    user_id: RequestUserDep,
    session_id: str,
    agent_id: str | None = None,
) -> SessionContextStatus:
    resolved = await resolve_session(
        ctx.store,
        session_id,
        agent_id=agent_id,
        user_id=user_id,
    )
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    scope, resolved_session_id = resolved
    from deep_agent.db.session_scope import agent_scope_to_api

    api_agent_id = agent_id or agent_scope_to_api(scope)
    history = await ctx.store.get_history(
        resolved_session_id,
        agent_id=api_agent_id,
    )
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    model_code: str | None = None
    model_base_url: str | None = None
    if api_agent_id:
        agent_row = await ctx.stores.agent.get_by_id(api_agent_id)
        if agent_row is not None:
            context_window_tokens = agent_row.llm_model.context_window_tokens
            max_output_tokens = agent_row.llm_model.max_output_tokens
            model_code = agent_row.llm_model.model
            model_base_url = agent_row.llm_model.base_url
    estimator = TokenEstimator.for_model(
        model_code or "",
        model_base_url,
    )
    budget = build_context_budget(
        history,
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
        estimator=estimator,
        output_history=history,
        estimate_scope="history_only",
    )
    checkpoint = await ctx.stores.context_checkpoint.get(
        resolved_session_id,
        agent_id=api_agent_id,
    )
    return SessionContextStatus(
        session_id=resolved_session_id,
        raw_message_count=len(history),
        estimated_history_tokens=budget.estimated_history_tokens,
        compaction_enabled=budget.enabled,
        compaction_reason=budget.reason,
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
        predicted_output_tokens=budget.predicted_output_tokens,
        output_prediction_source=budget.output_prediction_source,
        output_prediction_sample_count=budget.output_prediction_sample_count,
        provider_sample_count=budget.provider_sample_count,
        estimated_sample_count=budget.estimated_sample_count,
        estimator_source=budget.estimator_source,
        estimate_scope=budget.estimate_scope,
        trigger_tokens=budget.trigger_tokens,
        target_tokens=budget.target_tokens,
        checkpoint_exists=checkpoint is not None,
        checkpoint_source_last_sequence=(
            checkpoint.source_last_sequence if checkpoint else None
        ),
        checkpoint_estimated_tokens=(
            checkpoint.estimated_tokens if checkpoint else None
        ),
        checkpoint_updated_at=checkpoint.updated_at if checkpoint else None,
        summary_model_id=checkpoint.summary_model_id if checkpoint else None,
    )


@router.patch("/sessions/{session_id}", response_model=SessionInfo)
async def rename_session(
    ctx: AppContextDep,
    user_id: RequestUserDep,
    session_id: str,
    body: SessionRenameRequest,
    agent_id: str | None = None,
) -> SessionInfo:
    """重命名会话：手动设置标题或自动重新生成。

    - `{"title": "新名称"}` → 手动重命名
    - `{"auto": true}` → 基于对话内容自动重新生成标题
    """
    if body.title is not None and body.auto:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot specify both 'title' and 'auto'",
        )
    if body.title is None and not body.auto:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Must specify either 'title' or 'auto'",
        )

    resolved = await resolve_session(
        ctx.store,
        session_id,
        agent_id=agent_id,
        user_id=user_id,
    )
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    scope, resolved_session_id = resolved
    from deep_agent.db.session_scope import agent_scope_to_api

    api_agent_id = agent_id or agent_scope_to_api(scope)

    if body.title is not None:
        # 手动设置标题
        title = body.title.strip()[:80] if body.title.strip() else None
        await ctx.store.update_session_title(resolved_session_id, api_agent_id, title)
    elif body.auto:
        # 自动重新生成标题
        history = await ctx.store.get_history(resolved_session_id, agent_id=api_agent_id)
        if not history:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot auto-rename: session has no messages",
            )
        user_text = first_user_prompt(history) or ""
        assistant_text = _extract_assistant_text(history)

        from deep_agent.agent.title_generator import generate_title

        spec = await _spec_for_history(ctx, api_agent_id)
        title = await generate_title(
            user_text=user_text,
            assistant_text=assistant_text or user_text,
            spec=spec,
            auxiliary_store=ctx.stores.auxiliary_model,
            llm_model_store=ctx.stores.llm_model,
        )

        # LLM 失败时回退到文本提取
        if not title:
            title = user_text

        await ctx.store.update_session_title(resolved_session_id, api_agent_id, title)

    # 返回更新后的会话信息
    history = await ctx.store.get_history(resolved_session_id, agent_id=api_agent_id)
    return await _build_session_info(
        ctx,
        resolved_session_id,
        agent_id=api_agent_id,
        history=history,
    )
