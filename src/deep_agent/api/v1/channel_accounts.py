from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.services.resource_deletion import DeleteConfirmation, delete_resource
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
from deep_agent.schemas.llm_provider import mask_api_key
from deep_agent.channels.app_context import public_channel_settings
from deep_agent.services.channel_gateway_client import (
    ChannelGatewayClient,
    ChannelGatewayUnavailableError,
)


router = APIRouter()


@router.get("/channel-commands", dependencies=CONSOLE_ADMIN_DEPENDENCIES)
async def get_channel_commands() -> dict:
    from deep_agent.channels.commands import command_catalog

    return command_catalog()


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
        secret_masked=mask_api_key(row.secret),
        agent_id=row.agent_id,
        agent_name=agent.name if agent else None,
        session_user_id=row.session_user_id,
        enabled=row.enabled,
        settings=public_channel_settings(row.settings),
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
    if (current.settings or {}).get("wecom") != (row.settings or {}).get("wecom"):
        await store.refresh_wecom_titles(account_id)
    return await _response(row, ctx)


@router.delete(
    "/channel-accounts/{account_id}",
    dependencies=CONSOLE_ADMIN_DEPENDENCIES,
)
async def delete_channel_account(
    account_id: str,
    user: RequestUserDep,
    ctx: AppContextDep,
    body: DeleteConfirmation | None = None,
    store: ChannelStore = Depends(get_channel_store),
) -> dict[str, bool]:
    await delete_resource(ctx, store, account_id, user, body, channel=True)
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
            message="当前环境未部署渠道网关；可先保存渠道配置，部署网关并配置地址后需重启 DeepAgent API。",
        )
    try:
        payload = await client.statuses()
    except ChannelGatewayUnavailableError:
        return ChannelRuntimeResponse(
            deployed=True,
            reachable=False,
            state="unreachable",
            message="渠道网关已配置但当前不可达，请检查服务是否启动及内部连接配置。",
        )
    return ChannelRuntimeResponse(
        deployed=True,
        reachable=True,
        state=payload.get("state", "connected"),
        message=payload.get("message", "渠道网关运行正常"),
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
            detail="当前环境未部署渠道网关；请部署并配置网关地址后重启 DeepAgent API。",
        )
    try:
        payload = await client.test_account(account_id)
    except ChannelGatewayUnavailableError as exc:
        raise HTTPException(status_code=503, detail="渠道网关当前不可达") from exc
    return ChannelConnectionTestResponse.model_validate(payload)
