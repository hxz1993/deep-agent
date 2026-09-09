from __future__ import annotations

import hmac
import os
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.core.context import AppContextDep


CHANNEL_TOKEN_HEADER = "X-Deep-Agent-Channel-Token"


async def verify_optional_channel_internal_token(
    token: Annotated[str | None, Header(alias=CHANNEL_TOKEN_HEADER)] = None,
) -> None:
    """Authenticate Gateway calls without changing existing platform requests."""

    if token is None:
        return
    expected = os.environ.get("DEEP_AGENT_CHANNEL_INTERNAL_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Channel internal authentication is not configured",
        )
    if not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid channel internal token",
        )


async def resolve_channel_account(
    ctx: AppContextDep,
    token: Annotated[str | None, Header(alias=CHANNEL_TOKEN_HEADER)] = None,
    account_id: Annotated[str | None, Header(alias="X-Deep-Agent-Channel-Account")] = None,
):
    return await _load_channel_account(ctx, token, account_id, allow_disabled=False)


async def _load_channel_account(ctx, token, account_id, *, allow_disabled):
    if token is None and account_id is None:
        return None
    if not token:
        raise HTTPException(status_code=401, detail="Missing channel internal token")
    await verify_optional_channel_internal_token(token)
    if not account_id:
        raise HTTPException(status_code=422, detail="Missing channel account")
    account = await ctx.stores.channel.get_account(account_id) if ctx.stores.channel else None
    if account is None or (not allow_disabled and not account.enabled):
        raise HTTPException(status_code=403, detail="Channel account is unavailable")
    return account


ChannelAccountDep = Annotated[object | None, Depends(resolve_channel_account)]


async def resolve_channel_control_account(
    ctx: AppContextDep,
    token: Annotated[str | None, Header(alias=CHANNEL_TOKEN_HEADER)] = None,
    account_id: Annotated[str | None, Header(alias="X-Deep-Agent-Channel-Account")] = None,
):
    # Disabling an account must still allow its Gateway to cancel owned work.
    return await _load_channel_account(ctx, token, account_id, allow_disabled=True)


ChannelControlAccountDep = Annotated[object | None, Depends(resolve_channel_control_account)]


async def channel_request_user(user: RequestUserDep, account: ChannelAccountDep) -> str:
    # Only a token-authenticated Gateway can select the administrator-configured
    # session user. Normal chat identity and session access checks are unchanged.
    return account.session_user_id if account is not None else user


ChannelRequestUserDep = Annotated[str, Depends(channel_request_user)]
