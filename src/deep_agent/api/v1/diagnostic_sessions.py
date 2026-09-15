"""Administrator-only cross-user reads; ordinary Session ownership is unchanged."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.api.v1.diagnostic_routes import NoStoreRoute
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.api.v1.sessions import SessionEventsResponse, SessionEventResponse, _build_context_data, context_status_from_data
from deep_agent.capabilities.display_names import resolve_tool_display_name_map
from deep_agent.conversation.display_names import decorate_history_display_names
from deep_agent.conversation.models import ConversationHistoryResponse
from deep_agent.conversation.projector import ConversationProjectionError
from deep_agent.core.context import AppContextDep
from deep_agent.diagnostics.sessions import DiagnosticQueryError, DiagnosticSessionQueries, SessionFilters, detail_event_metadata, project_diagnostic_history, query_budget
from deep_agent.time import UtcDatetime, utc_rfc3339


class DiagnosticSessionRoute(NoStoreRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                return await original(request)
            except DiagnosticQueryError as exc:
                raise HTTPException(exc.status_code, str(exc), headers={"Cache-Control": "no-store"}) from exc
        return handle


router = APIRouter(prefix="/diagnostics/sessions", dependencies=CONSOLE_ADMIN_DEPENDENCIES, route_class=DiagnosticSessionRoute)


def filters(
    start: UtcDatetime | None = Query(default=None), end: UtcDatetime | None = Query(default=None),
    timezone: str = Query(default="UTC", max_length=64),
    agent_id: str | None = Query(default=None, max_length=256),
    user_id: str | None = Query(default=None, max_length=128),
    app_id: str | None = Query(default=None, max_length=256),
    query: str | None = Query(default=None, max_length=256),
    has_questions: bool | None = Query(default=None), has_execution_error: bool | None = Query(default=None),
    latest_status: Literal["running", "waiting", "completed", "stopped", "error", "unknown", "not_started"] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
):
    actual_end = end or datetime.now(UTC)
    return SessionFilters(start=start or actual_end - timedelta(days=7), end=actual_end, timezone=timezone,
        agent_id=agent_id, user_id=user_id, app_id=app_id, query=query, has_questions=has_questions,
        has_execution_error=has_execution_error, latest_status=latest_status, limit=limit, offset=offset)


FiltersDep = Annotated[SessionFilters, Depends(filters)]


def service(ctx):
    return DiagnosticSessionQueries(ctx.db_session_factory, ctx.active_runs.snapshot())


async def with_users(request, caller_user_id, result, user_ids):
    from deep_agent.security.diagnostic_users import resolve_diagnostic_users, DiagnosticUserAuthenticationError
    try:
        result["users"] = await resolve_diagnostic_users(request, caller_user_id, user_ids)
    except DiagnosticUserAuthenticationError as exc:
        raise HTTPException(exc.status_code, "平台用户资料认证失效，请重新登录") from exc
    return result


@router.get("/overview")
async def get_diagnostic_overview(request: Request, ctx: AppContextDep, caller_user_id: RequestUserDep, selected: FiltersDep):
    async with query_budget():
        result = await service(ctx).overview(selected)
    ids = {row["user_id"] for key in ("active_users", "recent_questions", "recent_errors") for row in result[key]}
    return await with_users(request, caller_user_id, result, ids)


@router.get("")
async def list_diagnostic_sessions(request: Request, ctx: AppContextDep, caller_user_id: RequestUserDep, selected: FiltersDep):
    async with query_budget():
        result = await service(ctx).list(selected)
    return await with_users(request, caller_user_id, result, {row["user_id"] for row in result["items"]})


@router.get("/users")
async def search_diagnostic_users(request: Request, ctx: AppContextDep, caller_user_id: RequestUserDep,
                                  selected: FiltersDep, after: str | None = Query(default=None, max_length=128)):
    async with query_budget():
        page = await service(ctx).user_option_ids(selected, after=after)
    result = await with_users(request, caller_user_id, {}, page["ids"])
    query = (selected.query or "").strip().casefold()
    items = [{"user_id": uid, "display_name": result["users"][uid]["display_name"]}
             for uid in page["ids"]
             if not query or query in uid.casefold()
             or query in result["users"][uid]["display_name"].casefold()]
    if query and not items and any(profile.get("resolution") == "unavailable" for profile in result["users"].values()):
        raise HTTPException(503, "用户名查询暂不可用，请稍后重试或使用完整 User ID")
    return {"items": items, "next_after": page["next_after"]}


@router.get("/{session_id}")
async def get_diagnostic_session(request: Request, ctx: AppContextDep, caller_user_id: RequestUserDep,
                                 session_id: str, include_context: bool = False):
    async with query_budget():
        result = await service(ctx).detail(session_id)
        events = await ctx.store.list_all_events(session_id)
        metadata = await run_in_threadpool(detail_event_metadata, events,
            result["run_id"] if result["latest_status"] == "running" else None)
        result.update(metadata, context_status=None, context_error=None)
        active = ctx.active_runs.get(agent_id=result["agent_id"], session_id=session_id)
        if active and active.user_id == result["user_id"] and active.run_id == result["run_id"]:
            result["active_run"] = {"run_id": active.run_id, "status": active.status, "started_at": utc_rfc3339(active.started_at)}
            if active.run_id and not any(run["run_id"] == active.run_id for run in result["runs"]):
                result["runs"].append({"run_id": active.run_id, "turn_id": None, "status": "running",
                    "start": utc_rfc3339(active.started_at), "end": None, "event_ids": []})
        if include_context:
            try:
                history = await ctx.store.get_history(session_id, agent_id=result["agent_id"])
                data = await _build_context_data(ctx, session_id, result["agent_id"], history, model_id=None, offload=True)
                context = context_status_from_data(session_id, data)
                context.manual_compaction_available = False
                result["context_status"] = context.model_dump(mode="json")
                result["context_status"]["estimate_basis"] = "current_configuration"
            except Exception:
                # No current model/provider/configuration must not hide history.
                # Context is a best-effort estimate, never a historical snapshot.
                result["context_error"] = "当前配置无法估算上下文；会话正文和轨迹仍可查看"
    return await with_users(request, caller_user_id, result, [result["user_id"]])


@router.get("/{session_id}/history", response_model=ConversationHistoryResponse)
async def get_diagnostic_history(ctx: AppContextDep, session_id: str):
    async with query_budget():
        detail = await service(ctx).identity(session_id)
        events = await ctx.store.list_all_events(session_id)
        try:
            projected = await run_in_threadpool(project_diagnostic_history, events, session_id)
        except ConversationProjectionError as exc:
            raise HTTPException(409, "会话正文投影失败，请切换轨迹查看已保存事件") from exc
        row = await ctx.stores.agent.get_by_id(detail["agent_id"]) if detail["agent_id"] else None
        names = await resolve_tool_display_name_map(ctx.registry, ctx.stores,
            capabilities=list(row.capabilities or []) if row else None)
        return await run_in_threadpool(decorate_history_display_names, projected, names)


@router.get("/{session_id}/events", response_model=SessionEventsResponse)
async def get_diagnostic_events(ctx: AppContextDep, session_id: str,
    after_sequence: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=500),
    event_type: str | None = Query(default=None, max_length=128),
    turn_id: str | None = Query(default=None, max_length=128),
    run_id: str | None = Query(default=None, max_length=128),
    call_id: str | None = Query(default=None, max_length=256)):
    async with query_budget():
        await service(ctx).identity(session_id)
        events = await ctx.store.list_events(session_id, after_sequence=after_sequence, limit=limit,
            event_type=event_type, turn_id=turn_id, run_id=run_id, call_id=call_id)
        items = await run_in_threadpool(lambda: [SessionEventResponse.model_validate(event.public_dict()) for event in events])
        return SessionEventsResponse(items=items, next_after_sequence=items[-1].sequence if len(items) == limit else None)
