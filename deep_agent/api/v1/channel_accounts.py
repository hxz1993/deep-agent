from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.core.context import AppContextDep
from deep_agent.db.deps import get_channel_store
from deep_agent.db.ids import normalize_agent_id
from deep_agent.db.stores.channel import ChannelStore
from deep_agent.schemas.channel import (
    ChannelAccountCreate,
    ChannelAccountResponse,
    ChannelAccountRuntimeStatus,
    ChannelAccountUpdate,
    ChannelConnectionTestResponse,
    ChannelRuntimeResponse,
)
from deep_agent.services.channel_gateway_client import (
    ChannelGatewayClient,
    ChannelGatewayUnavailableError,
)


router = APIRouter()


async def _ensure_agent(ctx: AppContextDep, agent_id: str) -> str:
    try:
        normalized = normalize_agent_id(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if await ctx.stores.agent.get_by_id(normalized) is None:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    return normalized


async def _response(row, ctx: AppContextDep) -> ChannelAccountResponse:
    agent = await ctx.stores.agent.get_by_id(row.agent_id)
    return ChannelAccountResponse(
        id=row.id,
        name=row.name,
        channel_type=row.channel_type,
        transport=row.transport,
        bot_id=row.bot_id,
        secret_set=bool(row.secret),
        agent_id=row.agent_id,
        agent_name=agent.name if agent else None,
        session_user_id=row.session_user_id,
        enabled=row.enabled,
        settings=row.settings or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get(
    "/channel-accounts",
    response_model=list[ChannelAccountResponse],
    dependencies=CONSOLE_ADMIN_DEPENDENCIES,
)
async def list_channel_accounts(
    ctx: AppContextDep,
    user: RequestUserDep,
    store: ChannelStore = Depends(get_channel_store),
) -> list[ChannelAccountResponse]:
    return [
        await _response(row, ctx)
        for row in await store.list_accounts()
    ]


@router.post(
    "/channel-accounts",
    response_model=ChannelAccountResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=CONSOLE_ADMIN_DEPENDENCIES,
)
async def create_channel_account(
    body: ChannelAccountCreate,
    ctx: AppContextDep,
    user: RequestUserDep,
    store: ChannelStore = Depends(get_channel_store),
) -> ChannelAccountResponse:
    agent_id = await _ensure_agent(ctx, body.agent_id)
    body = body.model_copy(update={"agent_id": agent_id})
    try:
        row = await store.create_account(body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _response(row, ctx)


@router.get(
    "/channel-accounts/{account_id}",
    response_model=ChannelAccountResponse,
    dependencies=CONSOLE_ADMIN_DEPENDENCIES,
)
async def get_channel_account(
    account_id: str,
    ctx: AppContextDep,
    user: RequestUserDep,
    store: ChannelStore = Depends(get_channel_store),
) -> ChannelAccountResponse:
    row = await store.get_account(account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Channel account not found")
    return await _response(row, ctx)


@router.put(
    "/channel-accounts/{account_id}",
    response_model=ChannelAccountResponse,
    dependencies=CONSOLE_ADMIN_DEPENDENCIES,
)
async def update_channel_account(
    account_id: str,
    body: ChannelAccountUpdate,
    ctx: AppContextDep,
    user: RequestUserDep,
    store: ChannelStore = Depends(get_channel_store),
) -> ChannelAccountResponse:
    current = await store.get_account(account_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Channel account not found")
    updates = body.model_dump(exclude_unset=True)
    if body.agent_id is not None:
        updates["agent_id"] = await _ensure_agent(ctx, body.agent_id)
    body = ChannelAccountUpdate.model_validate(updates)
    try:
        row = await store.update_account(account_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Channel account not found")
    return await _response(row, ctx)


@router.delete(
    "/channel-accounts/{account_id}",
    dependencies=CONSOLE_ADMIN_DEPENDENCIES,
)
async def delete_channel_account(
    account_id: str,
    user: RequestUserDep,
    store: ChannelStore = Depends(get_channel_store),
) -> dict[str, bool]:
    try:
        deleted = await store.delete_account(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Channel account not found")
    return {"deleted": True}


@router.get(
    "/channel-runtime",
    response_model=ChannelRuntimeResponse,
    dependencies=CONSOLE_ADMIN_DEPENDENCIES,
)
async def get_channel_runtime() -> ChannelRuntimeResponse:
    client = ChannelGatewayClient()
    if not client.configured:
        return ChannelRuntimeResponse(
            deployed=False,
            reachable=False,
            state="not_deployed",
            message="当前环境未部署 Channel Gateway；配置可保存，启用后需部署并重启服务。",
        )
    try:
        payload = await client.statuses()
    except ChannelGatewayUnavailableError:
        return ChannelRuntimeResponse(
            deployed=True,
            reachable=False,
            state="unreachable",
            message="Channel Gateway 已配置但当前不可达。",
        )
    return ChannelRuntimeResponse(
        deployed=True,
        reachable=True,
        state=payload.get("state", "connected"),
        message=payload.get("message", "Channel Gateway 运行正常"),
        accounts=[
            ChannelAccountRuntimeStatus.model_validate(item)
            for item in payload.get("accounts", [])
        ],
    )


@router.post(
    "/channel-accounts/{account_id}/test-connection",
    response_model=ChannelConnectionTestResponse,
    dependencies=CONSOLE_ADMIN_DEPENDENCIES,
)
async def test_channel_connection(
    account_id: str,
    user: RequestUserDep,
    store: ChannelStore = Depends(get_channel_store),
) -> ChannelConnectionTestResponse:
    if await store.get_account(account_id) is None:
        raise HTTPException(status_code=404, detail="Channel account not found")
    client = ChannelGatewayClient()
    if not client.configured:
        raise HTTPException(
            status_code=503,
            detail="当前环境未部署 Channel Gateway；请部署后重启 DeepAgent API。",
        )
    try:
        payload = await client.test_account(account_id)
    except ChannelGatewayUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Channel Gateway 当前不可达") from exc
    return ChannelConnectionTestResponse.model_validate(payload)
