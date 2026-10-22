from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.models.provider_registry import (
    ProviderRuntimeConfig,
    ProviderRuntimeError,
    effective_base_url,
    load_provider_for_model,
)
from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.services.resource_deletion import DeleteConfirmation, delete_resource
from deep_agent.core.context import AppContextDep
from deep_agent.capabilities.catalog import invalid_tool_package_codes
from deep_agent.capabilities.codes import canonical_tool_package_codes
from deep_agent.db.deps import get_agent_store, get_llm_model_store
from deep_agent.db.ids import normalize_agent_id
from deep_agent.db.stores.agent import AgentStoreBase
from deep_agent.db.stores.agent.base import InvalidAgentOutputConfig
from deep_agent.db.stores.llm_model import LlmModelStoreBase
from deep_agent.schemas.agent import (
    AgentCreate,
    AgentPromptEstimateRequest,
    AgentPromptEstimateResponse,
    AgentResponse,
    AgentUpdate,
    AgentOutputConfig,
    AgentOutputConfigResponse,
    AgentUpdater,
)
from deep_agent.security.diagnostic_users import DiagnosticUserAuthenticationError, resolve_diagnostic_users
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


async def _validate_output_name(config, capabilities, ctx) -> None:
    if config is None:
        return
    # Only existing catalogs are consulted here. Dynamic MCP disclosure is
    # checked again against the actual assembled tool definitions at Run time.
    capabilities = canonical_tool_package_codes(capabilities)
    selected = ctx.registry.selected(capabilities)
    known = {tool.name for package in selected for tool in package.tools}
    from deep_agent.capabilities.ui.builtin import builtin_ui_records
    known.update(tool.name for tool in builtin_ui_records() if tool.package_code in capabilities)
    for tool in await ctx.stores.ui_tool.list_all():
        if tool.package_code in capabilities:
            known.add(tool.name)
    if config.name in known:
        raise HTTPException(status_code=422, detail="output_config.name conflicts with an enabled tool")


@router.get(
    "/agents",
    response_model=list[AgentResponse],
    dependencies=CONSOLE_ADMIN_DEPENDENCIES,
)
async def list_agents(
    request: Request,
    user: RequestUserDep,
    store: AgentStoreBase = Depends(get_agent_store),
) -> list[AgentResponse]:
    rows = await store.list_all()
    try:
        profiles = await resolve_diagnostic_users(request, user, (row.updated_by for row in rows if row.updated_by))
    except DiagnosticUserAuthenticationError as exc:
        raise HTTPException(status_code=exc.status_code, detail="Platform account authorization rejected") from exc
    responses = [_agent_response(row) for row in rows]
    for response in responses:
        if response.updated_by:
            profile = profiles.get(response.updated_by, {})
            response.updated_by_user = AgentUpdater(
                id=response.updated_by, username=profile.get("username"),
                resolution=profile.get("resolution", "unavailable"),
            )
    return responses


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
    user: RequestUserDep,
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
    user: RequestUserDep,
    store: AgentStoreBase = Depends(get_agent_store),
    model_store: LlmModelStoreBase = Depends(get_llm_model_store),
) -> AgentResponse:
    await _validate_tool_packages(body.capabilities, ctx)
    await _validate_skills(body.skills, ctx)
    if body.output_config is not None:
        await _validate_output_name(body.output_config, body.capabilities, ctx)
    normalized_model_id = None
    if body.model_id is not None:
        normalized_model_id = await _ensure_model_selectable(model_store, body.model_id, ctx)
    body = body.model_copy(update={"model_id": normalized_model_id})
    existing = await store.get_by_name(body.name)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Agent already exists: {body.name}")
    try:
        row = await store.create_unique(body, user_id=user)
    except InvalidAgentOutputConfig as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
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
    user: RequestUserDep,
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
    output_config = body.output_config
    enabled = body.structured_output_enabled if "structured_output_enabled" in body.model_fields_set else current.structured_output_enabled
    if enabled and "output_config" not in body.model_fields_set:
        output_config = await store.get_output_config(normalized_id)
    if output_config is not None:
        await _validate_output_name(output_config, body.capabilities if body.capabilities is not None else current.capabilities, ctx)
    if body.model_id is not None and body.model_id != current.model_id:
        normalized_model_id = await _ensure_model_selectable(
            model_store,
            body.model_id,
            ctx,
        )
        body = body.model_copy(update={"model_id": normalized_model_id})
    try:
        row = await store.update_unique(normalized_id, body, user_id=user)
    except InvalidAgentOutputConfig as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
    return _agent_response(row)


@router.get(
    "/agents/{agent_id}/output-config",
    response_model=AgentOutputConfigResponse,
    dependencies=CONSOLE_ADMIN_DEPENDENCIES,
)
async def get_agent_output_config(
    agent_id: str,
    store: AgentStoreBase = Depends(get_agent_store),
) -> AgentOutputConfigResponse:
    normalized_id = _parse_agent_id(agent_id)
    row, config = await store.get_output_configuration(normalized_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
    return AgentOutputConfigResponse(
        structured_output_enabled=row.structured_output_enabled,
        allow_text_output=bool(config.allow_text_output) if config is not None else False,
        output_config=AgentOutputConfig.model_validate(config) if config is not None else None,
    )


@router.delete(
    "/agents/{agent_id}",
    status_code=status.HTTP_200_OK,
    dependencies=CONSOLE_ADMIN_DEPENDENCIES,
)
async def delete_agent(
    agent_id: str,
    ctx: AppContextDep,
    user: RequestUserDep,
    body: DeleteConfirmation | None = None,
    store: AgentStoreBase = Depends(get_agent_store),
) -> None:
    normalized_id = _parse_agent_id(agent_id)
    await delete_resource(ctx, store, normalized_id, user, body)
