from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.models.provider_registry import (
    ProviderRuntimeConfig,
    ProviderRuntimeError,
    effective_base_url,
    load_provider_for_model,
)
from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.core.context import AppContextDep
from deep_agent.capabilities.catalog import invalid_tool_package_codes
from deep_agent.db.deps import get_agent_store, get_llm_model_store
from deep_agent.db.ids import normalize_agent_id
from deep_agent.db.stores.agent import AgentStoreBase
from deep_agent.db.stores.llm_model import LlmModelStoreBase
from deep_agent.schemas.agent import (
    AgentCreate,
    AgentPromptEstimateRequest,
    AgentPromptEstimateResponse,
    AgentResponse,
    AgentUpdate,
)
from deep_agent.skills.runtime import validate_skill_codes

router = APIRouter()


def _parse_agent_id(agent_id: str) -> str:
    try:
        return normalize_agent_id(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def _parse_model_id(model_id: str) -> str:
    try:
        return normalize_agent_id(model_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def _agent_response(row) -> AgentResponse:
    return AgentResponse.from_row(row, row.llm_model)


async def _ensure_model_exists(model_store: LlmModelStoreBase, model_id: str) -> str:
    normalized_id = _parse_model_id(model_id)
    row = await model_store.get_by_id(normalized_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"LLM model not found: {model_id}")
    return normalized_id


async def _ensure_model_selectable(
    model_store: LlmModelStoreBase,
    model_id: str,
    ctx: AppContextDep,
) -> str:
    normalized_id = await _ensure_model_exists(model_store, model_id)
    model = await model_store.get_by_id(normalized_id)
    assert model is not None
    try:
        await load_provider_for_model(model, ctx.stores.llm_configuration)
    except ProviderRuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return normalized_id


async def _validate_tool_packages(
    capabilities: list[str],
    ctx: AppContextDep,
) -> None:
    invalid = await invalid_tool_package_codes(capabilities, ctx.registry, ctx.stores)
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Agent 包含未知或不存在的工具包",
                "invalid_tool_packages": invalid,
            },
        )


async def _validate_skills(
    skills: list[str],
    ctx: AppContextDep,
) -> None:
    invalid = await validate_skill_codes(ctx.stores, skills)
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Agent 包含未知或重复的 Skill",
                "invalid_skills": invalid,
            },
        )


@router.get(
    "/agents",
    response_model=list[AgentResponse],
    dependencies=CONSOLE_ADMIN_DEPENDENCIES,
)
async def list_agents(
    store: AgentStoreBase = Depends(get_agent_store),
) -> list[AgentResponse]:
    rows = await store.list_all()
    return [_agent_response(row) for row in rows]


@router.post(
    "/agents/prompt-estimate",
    response_model=AgentPromptEstimateResponse,
    dependencies=CONSOLE_ADMIN_DEPENDENCIES,
)
async def estimate_agent_prompt(
    body: AgentPromptEstimateRequest,
    ctx: AppContextDep,
) -> AgentPromptEstimateResponse:
    normalized_model_id = _parse_model_id(body.model_id)
    model = await ctx.stores.llm_model.get_by_id(normalized_model_id)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM model not found: {body.model_id}",
        )

    prompt = body.prompt.strip()
    provider = await load_provider_for_model(model, ctx.stores.llm_configuration)
    provider_config = ProviderRuntimeConfig.from_row(provider)
    estimator = TokenEstimator.for_model(
        model.model,
        effective_base_url(provider_config),
        provider_type=provider_config.provider_type,
    )
    return AgentPromptEstimateResponse(
        character_count=len(prompt),
        estimated_tokens=estimator.text(prompt),
        estimator_source=estimator.source,
    )


@router.get("/agents/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    store: AgentStoreBase = Depends(get_agent_store),
) -> AgentResponse:
    normalized_id = _parse_agent_id(agent_id)
    row = await store.get_by_id(normalized_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
    return _agent_response(row)


@router.post(
    "/agents",
    response_model=AgentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=CONSOLE_ADMIN_DEPENDENCIES,
)
async def create_agent(
    body: AgentCreate,
    ctx: AppContextDep,
    store: AgentStoreBase = Depends(get_agent_store),
    model_store: LlmModelStoreBase = Depends(get_llm_model_store),
) -> AgentResponse:
    await _validate_tool_packages(body.capabilities, ctx)
    await _validate_skills(body.skills, ctx)
    normalized_model_id = await _ensure_model_selectable(
        model_store,
        body.model_id,
        ctx,
    )
    body = body.model_copy(update={"model_id": normalized_model_id})
    existing = await store.get_by_name(body.name)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Agent already exists: {body.name}")
    try:
        row = await store.create_unique(body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _agent_response(row)


@router.patch(
    "/agents/{agent_id}",
    response_model=AgentResponse,
    dependencies=CONSOLE_ADMIN_DEPENDENCIES,
)
async def update_agent(
    agent_id: str,
    body: AgentUpdate,
    ctx: AppContextDep,
    store: AgentStoreBase = Depends(get_agent_store),
    model_store: LlmModelStoreBase = Depends(get_llm_model_store),
) -> AgentResponse:
    normalized_id = _parse_agent_id(agent_id)
    current = await store.get_by_id(normalized_id)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
    if "capabilities" in body.model_fields_set:
        await _validate_tool_packages(body.capabilities, ctx)
    if "skills" in body.model_fields_set:
        await _validate_skills(body.skills or [], ctx)
    if body.model_id is not None and body.model_id != current.model_id:
        normalized_model_id = await _ensure_model_selectable(
            model_store,
            body.model_id,
            ctx,
        )
        body = body.model_copy(update={"model_id": normalized_model_id})
    try:
        row = await store.update_unique(normalized_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
    return _agent_response(row)


@router.delete(
    "/agents/{agent_id}",
    status_code=status.HTTP_200_OK,
    dependencies=CONSOLE_ADMIN_DEPENDENCIES,
)
async def delete_agent(
    agent_id: str,
    ctx: AppContextDep,
    store: AgentStoreBase = Depends(get_agent_store),
) -> None:
    normalized_id = _parse_agent_id(agent_id)
    assert ctx.stores.channel is not None
    channel_accounts = await ctx.stores.channel.list_referencing_agent(normalized_id)
    if channel_accounts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent 正被渠道配置使用，请先调整或停用对应渠道",
        )
    try:
        deleted = await store.delete(normalized_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
