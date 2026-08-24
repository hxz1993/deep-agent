from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage

from deep_agent.core.context import AppContextDep
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.db.session_list import MAX_LIST_LIMIT, SessionListFilters, SessionSummaryRow
from deep_agent.db.session_scope import resolve_session
from deep_agent.db.ids import normalize_agent_id
from deep_agent.agent.history import (
    SessionHistoryResponse,
    HistoryContextCheckpoint,
    _extract_assistant_text,
    enrich_history_messages,
    first_user_prompt,
    model_messages_to_history,
    session_activity_timestamps,
)
from deep_agent.agent.run_identity import conversation_id_for
from deep_agent.agent.spec import AgentSpec, anonymous_spec
from deep_agent.agent.context.budget import build_context_budget
from deep_agent.agent.context.checkpoint import load_projection, projection_fingerprint
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.context.manual import manual_compaction_availability
from deep_agent.capabilities.mcp.resolver import load_mcp_rows
from deep_agent.capabilities.ui.registry import load_ui_tool_hints
from deep_agent.schemas.chat import SessionInfo, SessionListResponse
from deep_agent.options import OPTION

router = APIRouter()


class SessionCancelResponse(BaseModel):
    status: Literal["cancel_requested", "not_running"]
    session_id: str
    agent_id: str | None = None


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


@router.post("/sessions/{session_id}/cancel", response_model=SessionCancelResponse)
async def cancel_session_run(
    ctx: AppContextDep,
    user_id: RequestUserDep,
    session_id: str,
    agent_id: str | None = None,
) -> SessionCancelResponse:
    normalized_agent_id = agent_id
    if agent_id is not None:
        try:
            normalized_agent_id = normalize_agent_id(agent_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="agent_id must be a valid UUID",
            ) from exc

    # The stream registers before publishing its session SSE event and before
    # asynchronous user-message persistence is guaranteed to finish. Allow its
    # owner to cancel during that startup window without requiring a DB row.
    if ctx.active_runs.request_cancel(
        agent_id=normalized_agent_id,
        session_id=session_id,
        user_id=user_id,
    ):
        return SessionCancelResponse(
            status="cancel_requested",
            session_id=session_id,
            agent_id=normalized_agent_id,
        )

    resolved = await resolve_session(
        ctx.store,
        session_id,
        agent_id=normalized_agent_id,
        user_id=user_id,
    )
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    scope, resolved_session_id = resolved
    from deep_agent.db.session_scope import agent_scope_to_api

    api_agent_id = agent_scope_to_api(scope)
    requested = ctx.active_runs.request_cancel(
        agent_id=api_agent_id,
        session_id=resolved_session_id,
        user_id=user_id,
    )
    return SessionCancelResponse(
        status="cancel_requested" if requested else "not_running",
        session_id=resolved_session_id,
        agent_id=api_agent_id,
    )


async def _spec_for_history(ctx: AppContextDep, agent_id: str | None) -> AgentSpec:
    if agent_id:
        row = await ctx.stores.agent.get_by_id(agent_id)
        if row is not None:
            from deep_agent.agent.provider_registry import load_provider_for_model

            provider = await load_provider_for_model(
                row.llm_model,
                ctx.stores.llm_configuration,
            )
            return AgentSpec.from_row(row, row.llm_model, provider=provider)
    return anonymous_spec()


@router.get("/sessions/{session_id}/history", response_model=SessionHistoryResponse)
async def get_session_history(
    ctx: AppContextDep,
    user_id: RequestUserDep,
    session_id: str,
    agent_id: str | None = None,
    model_id: str | None = None,
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
    context_data = await _build_context_data(
        ctx,
        resolved_session_id,
        api_agent_id,
        raw,
        model_id=model_id,
    )
    return SessionHistoryResponse(
        session_id=resolved_session_id,
        conversation_id=conversation_id_for(api_agent_id, resolved_session_id),
        messages=messages,
        model_message_count=len(raw),
        context_checkpoint=context_data["history_checkpoint"],
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
    safety_margin_tokens: int | None = None
    usable_request_tokens: int | None = None
    trigger_tokens: int | None = None
    target_tokens: int | None = None
    checkpoint_exists: bool = False
    checkpoint_active: bool = False
    checkpoint_invalid_reason: str | None = None
    checkpoint_source_last_sequence: int | None = None
    checkpoint_estimated_tokens: int | None = None
    checkpoint_updated_at: datetime | None = None
    summary_model_id: str | None = None
    checkpoint: HistoryContextCheckpoint | None = None
    manual_compaction_available: bool = False
    manual_compaction_reason: str = "context_window_unknown"


async def _context_model(
    ctx: AppContextDep,
    agent_id: str | None,
    model_id: str | None,
):
    effective_model_id = model_id
    if effective_model_id is None and agent_id:
        agent_row = await ctx.stores.agent.get_by_id(agent_id)
        if agent_row is not None:
            effective_model_id = agent_row.model_id
    if effective_model_id is None:
        return None
    row = await ctx.stores.llm_model.get_by_id(effective_model_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM model not found: {effective_model_id}",
        )
    return row


async def _build_context_data(
    ctx: AppContextDep,
    session_id: str,
    agent_id: str | None,
    history: list[ModelMessage],
    *,
    model_id: str | None,
) -> dict[str, Any]:
    model = await _context_model(ctx, agent_id, model_id)
    context_window_tokens = model.context_window_tokens if model is not None else None
    max_output_tokens = model.max_output_tokens if model is not None else None
    if model is not None:
        from deep_agent.agent.provider_registry import (
            ProviderRuntimeConfig,
            effective_base_url,
            load_provider_for_model,
        )

        provider = await load_provider_for_model(
            model,
            ctx.stores.llm_configuration,
        )
        provider_config = ProviderRuntimeConfig.from_row(provider)
        estimator = TokenEstimator.for_model(
            model.model,
            effective_base_url(provider_config),
            provider_type=provider_config.provider_type,
        )
    else:
        estimator = TokenEstimator.for_model("")
    budget = build_context_budget(
        history,
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
        estimator=estimator,
        output_history=history,
        estimate_scope="history_only",
    )
    checkpoint = await ctx.stores.context_checkpoint.get(
        session_id,
        agent_id=agent_id,
    )
    fingerprint = projection_fingerprint(
        llm_model_id=model.id if model is not None else None,
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
        trigger_ratio=OPTION.agent.context_compaction_trigger_ratio,
        target_ratio=OPTION.agent.context_compaction_target_ratio,
        keep_tool_pairs=OPTION.agent.context_keep_tool_pairs,
        summary_model_id=model.id if model is not None else None,
        estimator_source=estimator.source,
    )
    projection = await load_projection(
        ctx.stores.context_checkpoint,
        history,
        session_id=session_id,
        agent_id=agent_id,
        fingerprint=fingerprint,
    )
    availability_budget = build_context_budget(
        projection.messages,
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
        estimator=estimator,
        output_history=history,
        estimate_scope="history_only",
    )
    availability = manual_compaction_availability(
        history,
        availability_budget,
    )

    checkpoint_view: dict[str, Any] | None = None
    history_checkpoint: dict[str, Any] | None = None
    if projection.used_checkpoint and checkpoint is not None:
        source_count = checkpoint.source_last_sequence + 1
        source_tokens = estimator.messages(history[:source_count])
        projected_tokens = checkpoint.estimated_tokens
        reduction_ratio = (
            max(0.0, 1.0 - projected_tokens / source_tokens)
            if source_tokens > 0
            else 0.0
        )
        checkpoint_view = {
            "source_last_sequence": checkpoint.source_last_sequence,
            "covered_model_message_count": source_count,
            "source_estimated_tokens": source_tokens,
            "projected_estimated_tokens": projected_tokens,
            "reduction_ratio": reduction_ratio,
            "new_message_count": max(0, len(history) - source_count),
            "summary_model_id": checkpoint.summary_model_id,
            "row_version": checkpoint.row_version,
            "updated_at": checkpoint.updated_at,
        }
        history_checkpoint = dict(checkpoint_view)

    return {
        "budget": availability_budget if projection.used_checkpoint else budget,
        "context_window_tokens": context_window_tokens,
        "max_output_tokens": max_output_tokens,
        "checkpoint": checkpoint,
        "checkpoint_active": projection.used_checkpoint,
        "checkpoint_invalid_reason": projection.invalid_reason,
        "checkpoint_view": checkpoint_view,
        "history_checkpoint": history_checkpoint,
        "manual_compaction_available": availability.available,
        "manual_compaction_reason": availability.reason,
    }


@router.get(
    "/sessions/{session_id}/context-status",
    response_model=SessionContextStatus,
)
async def get_session_context_status(
    ctx: AppContextDep,
    user_id: RequestUserDep,
    session_id: str,
    agent_id: str | None = None,
    model_id: str | None = None,
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
    context_data = await _build_context_data(
        ctx,
        resolved_session_id,
        api_agent_id,
        history,
        model_id=model_id,
    )
    budget = context_data["budget"]
    checkpoint = context_data["checkpoint"]
    return SessionContextStatus(
        session_id=resolved_session_id,
        raw_message_count=len(history),
        estimated_history_tokens=budget.estimated_history_tokens,
        compaction_enabled=budget.enabled,
        compaction_reason=budget.reason,
        context_window_tokens=context_data["context_window_tokens"],
        max_output_tokens=context_data["max_output_tokens"],
        predicted_output_tokens=budget.predicted_output_tokens,
        output_prediction_source=budget.output_prediction_source,
        output_prediction_sample_count=budget.output_prediction_sample_count,
        provider_sample_count=budget.provider_sample_count,
        estimated_sample_count=budget.estimated_sample_count,
        estimator_source=budget.estimator_source,
        estimate_scope=budget.estimate_scope,
        safety_margin_tokens=budget.safety_margin_tokens,
        usable_request_tokens=budget.usable_request_tokens,
        trigger_tokens=budget.trigger_tokens,
        target_tokens=budget.target_tokens,
        checkpoint_exists=checkpoint is not None,
        checkpoint_active=context_data["checkpoint_active"],
        checkpoint_invalid_reason=context_data["checkpoint_invalid_reason"],
        checkpoint_source_last_sequence=(
            checkpoint.source_last_sequence if checkpoint else None
        ),
        checkpoint_estimated_tokens=(
            checkpoint.estimated_tokens if checkpoint else None
        ),
        checkpoint_updated_at=checkpoint.updated_at if checkpoint else None,
        summary_model_id=checkpoint.summary_model_id if checkpoint else None,
        checkpoint=context_data["checkpoint_view"],
        manual_compaction_available=context_data["manual_compaction_available"],
        manual_compaction_reason=context_data["manual_compaction_reason"],
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
            llm_configuration_store=ctx.stores.llm_configuration,
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
