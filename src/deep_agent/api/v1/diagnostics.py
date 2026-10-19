"""Enterprise-admin diagnostics; separate from conversation history permissions."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.api.v1.diagnostic_routes import NoStoreRoute
from deep_agent.diagnostics.query import QueryError, logs_status, query_logs
from deep_agent.schemas.diagnostic_query import DiagnosticLogsQuery

router = APIRouter(prefix="/diagnostics/logs", dependencies=CONSOLE_ADMIN_DEPENDENCIES, route_class=NoStoreRoute)
tag = "diagnostics"


def _runtime():
    from deep_agent.diagnostics.runtime import config_from_env, get_runtime
    runtime = get_runtime()
    return (runtime.config if runtime else config_from_env()), runtime


@router.get("/status")
async def get_logs_status():
    config, runtime = _runtime()
    try:
        return await run_in_threadpool(logs_status, config, runtime)
    except QueryError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code}) from exc


@router.post("")
async def query_diagnostic_logs(body: DiagnosticLogsQuery):
    return await get_logs(**body.model_dump())


@router.get("")
async def get_logs(
    start: str | None = Query(None, max_length=40), end: str | None = Query(None, max_length=40),
    source: Literal["api", "gateway"] | None = None,
    level: str | None = Query(None, max_length=16),
    session_id: str | None = Query(None, max_length=128),
    run_id: str | None = Query(None, max_length=128),
    q: str | None = Query(None, max_length=512),
    limit: int = Query(200, ge=1, le=500),
    cursor: str | None = Query(None, max_length=64),
):
    config, _ = _runtime()
    try:
        return await run_in_threadpool(query_logs, config, start=start, end=end, source=source,
                                       level=level, session_id=session_id, run_id=run_id, q=q,
                                       limit=limit, cursor=cursor)
    except QueryError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code}) from exc
