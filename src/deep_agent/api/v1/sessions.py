from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_ai.messages import ModelMessage

from deep_agent.core.context import AppContextDep
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.api.v1.channel_auth import ChannelControlAccountDep
from deep_agent.db.session_list import MAX_LIST_LIMIT, SessionListFilters, SessionSummaryRow
from deep_agent.db.session_scope import resolve_session
from deep_agent.db.ids import normalize_agent_id
from deep_agent.agent.history import (
    first_user_prompt,
    session_activity_timestamps,
)
from deep_agent.agent.context.budget import build_context_budget
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.context.manual import manual_compaction_availability
from deep_agent.conversation.models import ConversationHistoryResponse
from deep_agent.conversation.display_names import decorate_history_display_names
from deep_agent.conversation.projector import ConversationProjectionError, project_session_events
from deep_agent.capabilities.display_names import resolve_tool_display_name_map
from deep_agent.schemas.chat import ChannelSessionOrigin, SessionInfo, SessionListResponse
from deep_agent.time import UtcDatetime

router = APIRouter()


class SessionCancelResponse(BaseModel):
    status: Literal["cancel_requested", "not_running"]
    session_id: str
    agent_id: str | None = None


class SessionRunStatusResponse(BaseModel):
    status: Literal["idle", "running", "cancel_requested"]
    session_id: str
    agent_id: str | None = None
    run_id: str | None = None
    started_at: UtcDatetime | None = None


async def _channel_control_scope(ctx, account, session_id, agent_id, user_id):
    if account is None:
        return agent_id, user_id
    session = await ctx.stores.channel.get_channel_session(account.id, session_id)
    if session is None or (agent_id is not None and agent_id != session.agent_id):
        raise HTTPException(status_code=404, detail="Session not found")
    # Ownership is a verified channel relationship, never a user-supplied
    # header. Use the actual old Session identity after configuration changes.
    return session.agent_id, session.user_id


async def _resolve_agent_name(ctx: AppContextDep, agent_id: str | None) -> str | None:
    if agent_id is None:
        return None
    row = await ctx.stores.agent.get_by_id(agent_id)
    return row.name if row else None


def _summary_to_session_info(row: SessionSummaryRow) -> SessionInfo:
    return SessionInfo(
        session_id=row.session_id,
        message_count=row.message_count,
        turn_count=row.turn_count,
        attachment_count=row.attachment_count,
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
        channel=await ctx.stores.channel.get_session_origin(session_id) if ctx.stores.channel else None,
    )


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    ctx: AppContextDep,
    user_id: RequestUserDep,
    agent_id: str | None = Query(default=None, description="Filter by agent id."),
    limit: int = Query(default=50, ge=1, le=MAX_LIST_LIMIT, description="Page size."),
    offset: int = Query(default=0, ge=0, description="Page offset."),
    updated_after: UtcDatetime | None = Query(default=None, description="Only sessions updated at or after this time (UTC)."),
    updated_before: UtcDatetime | None = Query(default=None, description="Only sessions updated at or before this time (UTC)."),
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
    origins = await ctx.stores.channel.get_session_origins([row.session_id for row in rows]) if ctx.stores.channel else {}
    items = [_summary_to_session_info(row) for row in rows]
    for item in items:
        origin = origins.get(item.session_id)
        item.channel = ChannelSessionOrigin.model_validate(origin) if origin else None
    return SessionListResponse(
        items=items,
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
    channel_account: ChannelControlAccountDep,
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

    normalized_agent_id, user_id = await _channel_control_scope(
        ctx, channel_account, session_id, normalized_agent_id, user_id,
    )
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


@router.get("/sessions/{session_id}/run-status", response_model=SessionRunStatusResponse)
async def get_session_run_status(
    ctx: AppContextDep,
    user_id: RequestUserDep,
    channel_account: ChannelControlAccountDep,
    session_id: str,
    agent_id: str | None = None,
) -> SessionRunStatusResponse:
    if agent_id is not None:
        try:
            agent_id = normalize_agent_id(agent_id)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="agent_id must be a valid UUID") from exc
    agent_id, user_id = await _channel_control_scope(ctx, channel_account, session_id, agent_id, user_id)
    active = ctx.active_runs.get(agent_id=agent_id, session_id=session_id)
    if active is None or active.user_id != user_id:
        resolved = await resolve_session(ctx.store, session_id, agent_id=agent_id, user_id=user_id)
        if resolved is None:
            raise HTTPException(status_code=404, detail="Session not found")
        from deep_agent.db.session_scope import agent_scope_to_api

        agent_id = agent_scope_to_api(resolved[0])
        active = ctx.active_runs.get(agent_id=agent_id, session_id=session_id)
    if active is not None and active.user_id == user_id:
        return SessionRunStatusResponse(status=active.status, session_id=session_id, agent_id=agent_id, run_id=active.run_id, started_at=active.started_at)
    return SessionRunStatusResponse(status="idle", session_id=session_id, agent_id=agent_id)


class SessionEventResponse(BaseModel):
    event_id: str
    session_id: str
    sequence: int
    schema_version: int
    event_type: str
    status: str | None = None
    turn_id: str | None = None
    run_id: str | None = None
    payload: dict[str, Any]
    created_at: UtcDatetime
    updated_at: UtcDatetime


class SessionEventsResponse(BaseModel):
    items: list[SessionEventResponse]
    next_after_sequence: int | None = None


@router.get(
    "/sessions/{session_id}/history",
    response_model=ConversationHistoryResponse,
)
async def get_session_history(
    ctx: AppContextDep,
    user_id: RequestUserDep,
    session_id: str,
    agent_id: str | None = None,
) -> ConversationHistoryResponse:
    resolved = await resolve_session(
        ctx.store,
        session_id,
        agent_id=agent_id,
        user_id=user_id,
    )
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    scope, resolved_session_id = resolved
    events = await ctx.store.list_all_events(resolved_session_id)
    try:
        projected = project_session_events(events, session_id=resolved_session_id)
    except ConversationProjectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    from deep_agent.db.session_scope import agent_scope_to_api

    api_agent_id = agent_scope_to_api(scope)
    row = (
        await ctx.stores.agent.get_by_id(api_agent_id)
        if api_agent_id is not None
        else None
    )
    display_names = await resolve_tool_display_name_map(
        ctx.registry,
        ctx.stores,
        capabilities=(list(row.capabilities or []) if row is not None else None),
    )
    return decorate_history_display_names(projected, display_names)


@router.get("/sessions/{session_id}/events", response_model=SessionEventsResponse)
async def get_session_events(
    ctx: AppContextDep,
    user_id: RequestUserDep,
    session_id: str,
    agent_id: str | None = None,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    event_type: str | None = None,
    turn_id: str | None = None,
    run_id: str | None = None,
    call_id: str | None = None,
) -> SessionEventsResponse:
    resolved = await resolve_session(
        ctx.store, session_id, agent_id=agent_id, user_id=user_id
    )
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    _scope, resolved_session_id = resolved
    events = await ctx.store.list_events(
        resolved_session_id,
        after_sequence=after_sequence,
        limit=limit,
        event_type=event_type,
        turn_id=turn_id,
        run_id=run_id,
        call_id=call_id,
    )
    items = [SessionEventResponse.model_validate(event.public_dict()) for event in events]
    return SessionEventsResponse(
        items=items,
        next_after_sequence=items[-1].sequence if len(items) == limit else None,
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
    """Manual session rename. Automatic regeneration is intentionally absent."""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=80)

    @field_validator("title")
    @classmethod
    def _non_blank_title(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("Session title must not be blank")
        return title


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
    compaction_event_id: str | None = None
    compaction_source_through_sequence: int | None = None
    compaction_estimated_tokens: int | None = None
    compaction_updated_at: UtcDatetime | None = None
    summary_model_id: str | None = None
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
        from deep_agent.models.provider_registry import (
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
    latest_loader = getattr(ctx.store, "latest_completed_compaction", None)
    compaction = await latest_loader(session_id) if callable(latest_loader) else None
    raw_reader = getattr(ctx.store, "get_raw_history", None)
    raw_history = (
        await raw_reader(session_id, agent_id=agent_id)
        if callable(raw_reader)
        else history
    )
    availability = manual_compaction_availability(
        raw_history,
        budget,
    )
    return {
        "budget": budget,
        "context_window_tokens": context_window_tokens,
        "max_output_tokens": max_output_tokens,
        "compaction": compaction,
        "raw_history": raw_history,
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
    compaction = context_data["compaction"]
    compaction_source = compaction.payload.get("source", {}) if compaction else {}
    compaction_budget = compaction.payload.get("budget", {}) if compaction else {}
    summary_call = compaction.payload.get("summary_call", {}) if compaction else {}
    return SessionContextStatus(
        session_id=resolved_session_id,
        raw_message_count=len(context_data["raw_history"]),
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
        compaction_event_id=compaction.event_id if compaction else None,
        compaction_source_through_sequence=(
            int(compaction_source.get("through_sequence"))
            if compaction_source.get("through_sequence") is not None else None
        ),
        compaction_estimated_tokens=(
            int(compaction_budget.get("estimated_after"))
            if compaction_budget.get("estimated_after") is not None else None
        ),
        compaction_updated_at=compaction.updated_at if compaction else None,
        summary_model_id=summary_call.get("model_id"),
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
    """Set a non-blank manual title and append its audit Event."""

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

    title = body.title.strip()
    if not title:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Session title must not be blank",
        )
    await ctx.store.apply_session_title(
        resolved_session_id,
        agent_id=api_agent_id,
        user_id=user_id,
        title=title,
        source="user",
        source_event_id=None,
        turn_id=None,
        run_id=None,
    )

    # 返回更新后的会话信息
    history = await ctx.store.get_history(resolved_session_id, agent_id=api_agent_id)
    return await _build_session_info(
        ctx,
        resolved_session_id,
        agent_id=api_agent_id,
        history=history,
    )
