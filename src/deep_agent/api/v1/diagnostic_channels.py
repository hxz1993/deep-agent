"""Administrator-only channel sender diagnostics; no configuration writes."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.api.v1.diagnostic_sessions import (
    DiagnosticSessionRoute,
    filters,
    with_users,
)
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.core.context import AppContextDep
from deep_agent.diagnostics.channels import DiagnosticChannelQueries
from deep_agent.services.channel_profiles import ChannelProfileService
from deep_agent.diagnostics.sessions import SessionFilters, query_budget

router = APIRouter(
    prefix="/diagnostics/channels",
    dependencies=CONSOLE_ADMIN_DEPENDENCIES,
    route_class=DiagnosticSessionRoute,
)
FiltersDep = Annotated[SessionFilters, Depends(filters)]


@router.get("/accounts")
async def channel_accounts(
    request: Request, ctx: AppContextDep, caller_user_id: RequestUserDep
):
    async with query_budget():
        result = await DiagnosticChannelQueries(ctx.db_session_factory, profiles=ChannelProfileService(ctx)).accounts()
    result = await with_users(
        request, caller_user_id, result, {r["user_id"] for r in result["items"]}
    )
    for item in result["items"]:
        user = result["users"].get(item["user_id"])
        item["user_name"] = user.get("display_name") if isinstance(user, dict) else None
    return result


@router.get("/{account_id}/overview")
async def channel_overview(ctx: AppContextDep, account_id: str, selected: FiltersDep):
    async with query_budget():
        return await DiagnosticChannelQueries(ctx.db_session_factory, profiles=ChannelProfileService(ctx)).overview(
            account_id, selected
        )


@router.get("/{account_id}/peers")
async def channel_peers(
    ctx: AppContextDep,
    account_id: str,
    selected: FiltersDep,
    kind: Literal["user", "group"] = "user",
    options_only: bool = False,
    include_group_senders: bool = False,
    sender_id: str | None = Query(default=None, min_length=1, max_length=256),
    peer_id: str | None = Query(default=None, min_length=1, max_length=256),
):
    async with query_budget(timeout_seconds=18 if selected.query and kind == "user" else 8):
        return await DiagnosticChannelQueries(ctx.db_session_factory, profiles=ChannelProfileService(ctx)).peers(
            account_id, selected, kind, options_only=options_only, peer_id=peer_id, sender_id=sender_id, include_group_senders=include_group_senders
        )


@router.get("/{account_id}/sessions")
async def channel_sessions(
    ctx: AppContextDep,
    account_id: str,
    selected: FiltersDep,
    peer_id: str = Query(min_length=1, max_length=256),
    sender_id: str | None = Query(default=None, min_length=1, max_length=256),
    kind: Literal["user", "group"] = "user",
):
    async with query_budget():
        return await DiagnosticChannelQueries(ctx.db_session_factory, profiles=ChannelProfileService(ctx)).sessions(
            account_id, selected, kind, peer_id, sender_id=sender_id
        )
