"""Administrator-only historical model usage statistics."""
from __future__ import annotations

import csv
from datetime import UTC, datetime, time, timedelta
from io import StringIO
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.api.v1.diagnostic_routes import NoStoreRoute
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.core.context import AppContextDep
from deep_agent.diagnostics.sessions import DiagnosticQueryError, SessionFilters, query_budget
from deep_agent.diagnostics.usage import DIMENSIONS, UsageQueries
from deep_agent.security.diagnostic_users import resolve_diagnostic_users, DiagnosticUserAuthenticationError
from deep_agent.time import UtcDatetime


class UsageRoute(NoStoreRoute):
    def get_route_handler(self):
        parent = super().get_route_handler()
        async def handle(request):
            try: return await parent(request)
            except DiagnosticQueryError as exc: raise HTTPException(exc.status_code, str(exc)) from exc
        return handle


router = APIRouter(prefix="/diagnostics/usage", dependencies=CONSOLE_ADMIN_DEPENDENCIES, route_class=UsageRoute)
Dimension = Literal["model", "user", "agent", "date"]
Sort = Literal["total_tokens", "input_tokens", "output_tokens", "requests"]


def filters(start: UtcDatetime | None = None, end: UtcDatetime | None = None,
            timezone: str = Query(default="UTC", max_length=64),
            agent_id: str | None = Query(default=None, max_length=256)):
    actual_end = end or datetime.now(UTC)
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise DiagnosticQueryError("无效的时区") from exc
    default_start = datetime.combine(actual_end.astimezone(zone).date() - timedelta(days=6),
        time.min, zone).astimezone(UTC)
    return SessionFilters(start=start or default_start, end=actual_end,
        timezone=timezone, agent_id=agent_id)


FiltersDep = Annotated[SessionFilters, Depends(filters)]


async def user_profiles(request, caller, ids):
    try: return await resolve_diagnostic_users(request, caller, ids)
    except DiagnosticUserAuthenticationError as exc:
        raise HTTPException(exc.status_code, "平台用户资料认证失效，请重新登录") from exc


def enrich(rows, dimension, report):
    if dimension == "model":
        for row in rows:
            key = row["key"]
            snapshot = report["model_snapshots"].get(key, {})
            model = report["models"].get(key)
            row["model"] = ({**snapshot, **{field: value for field, value in model.items() if value is not None},
                "deleted": False} if model else {**snapshot, "deleted": not key.startswith("legacy:")})
    if dimension == "agent":
        for row in rows: row["name"] = report["agents"].get(row["key"], row["key"] or "未记录 Agent")
    return rows


async def report_data(ctx, selected):
    async with query_budget():
        return await UsageQueries(ctx.db_session_factory).report(selected)


@router.get("")
async def get_usage(request: Request, ctx: AppContextDep, caller: RequestUserDep, selected: FiltersDep):
    result = await report_data(ctx, selected)
    ids = [row["key"] for row in sorted(result["groups"]["user"],
        key=lambda row: -row["total_tokens"])[:5]]
    result["users"] = await user_profiles(request, caller, ids)
    # The report exposes only top series and compact distribution; detail rows
    # are separately paginated so a large user directory is never sent at once.
    result["distribution"] = sorted(result["groups"]["purpose"], key=lambda row: -row["total_tokens"])
    result["top_users"] = sorted(result["groups"]["user"], key=lambda row: -row["total_tokens"])[:5]
    result.pop("groups")
    return result


async def detail_data(request, ctx, caller, selected, dimension, sort, descending, limit, offset):
    report = await report_data(ctx, selected)
    rows = enrich(report["groups"][dimension], dimension, report)
    profiles = {}
    rows.sort(key=lambda row: (row[sort], row["key"]), reverse=descending)
    selected_rows = rows[offset:offset + limit] if limit else rows
    if dimension == "user":
        profiles.update(await user_profiles(request, caller, [row["key"] for row in selected_rows]))
    return {"items": selected_rows, "total": len(rows), "users": profiles,
        "range": report["range"], "summary": report["summary"]}


@router.get("/details")
async def get_details(request: Request, ctx: AppContextDep, caller: RequestUserDep,
                      selected: FiltersDep, group_by: Dimension = "model",
                      sort: Sort = "total_tokens",
                      descending: bool = True, limit: int = Query(default=10, ge=1, le=50),
                      offset: int = Query(default=0, ge=0)):
    return await detail_data(request, ctx, caller, selected, group_by, sort, descending, limit, offset)


@router.get("/sessions")
async def get_contributing_sessions(ctx: AppContextDep, selected: FiltersDep,
                                    group_by: Dimension, key: str = Query(max_length=256),
                                    offset: int = Query(default=0, ge=0)):
    async with query_budget():
        rows, total = await UsageQueries(ctx.db_session_factory).sessions(
            selected, dimension=group_by, key=key, offset=offset)
    return {"items": rows, "total": total}


@router.get("/export")
async def export_details(request: Request, ctx: AppContextDep, caller: RequestUserDep,
                         selected: FiltersDep, group_by: Dimension = "model",
                         sort: Sort = "total_tokens",
                         descending: bool = True, scope: Literal["all", "page"] = "all",
                         offset: int = Query(default=0, ge=0)):
    detail = await detail_data(request, ctx, caller, selected, group_by,
        sort, descending, 10 if scope == "page" else None, offset if scope == "page" else 0)
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["维度", "ID", "名称", "邮箱", "Provider", "请求次数", "输入 Token", "输出 Token", "合计 Token", "缺失请求"])
    for row in detail["items"]:
        profile = detail["users"].get(row["key"], {}) if group_by == "user" else {}
        model = row.get("model") or {}
        writer.writerow([group_by, row["key"], profile.get("username") or row.get("name") or model.get("name") or model.get("model_name") or "",
            profile.get("email") or "", model.get("provider_name") or "", row["requests"],
            row["input_tokens"], row["output_tokens"], row["total_tokens"], row["missing_requests"]])
    return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=deepagent-usage.csv"})
