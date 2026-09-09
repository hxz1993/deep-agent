from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from deep_agent.models.provider_registry import PROVIDER_TYPES, provider_type_definition
from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.db.deps import get_llm_configuration_service
from deep_agent.db.ids import normalize_provider_id
from deep_agent.schemas.llm_provider import (
    LlmProviderCreate,
    LlmProviderResponse,
    LlmProviderTypeItem,
    LlmProviderTypesResponse,
    LlmProviderUpdate,
    mask_api_key,
)
from deep_agent.services.llm_configuration import (
    LlmConfigurationConflictError,
    LlmConfigurationService,
    LlmConfigurationValidationError,
)


router = APIRouter(dependencies=CONSOLE_ADMIN_DEPENDENCIES)


def _parse_provider_id(provider_id: str) -> str:
    try:
        return normalize_provider_id(provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


async def _response(
    service: LlmConfigurationService,
    row,
) -> LlmProviderResponse:
    definition = provider_type_definition(row.provider_type)
    return LlmProviderResponse(
        id=row.id,
        name=row.name,
        provider_type=row.provider_type,
        provider_type_label=definition.label,
        use_default_base_url=row.use_default_base_url,
        base_url=row.base_url,
        api_key_set=bool(row.api_key),
        api_key_masked=mask_api_key(row.api_key),
        model_count=await service.count_provider_models(row.id),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/llm-provider-types", response_model=LlmProviderTypesResponse)
async def list_llm_provider_types() -> LlmProviderTypesResponse:
    return LlmProviderTypesResponse(items=[
        LlmProviderTypeItem(
            code=item.code,
            label=item.label,
            order=item.order,
            default_base_url=item.default_base_url,
            supports_default_base_url=item.supports_default_base_url,
            supports_custom_base_url=item.supports_custom_base_url,
            model_protocol=item.model_protocol,
        )
        for item in PROVIDER_TYPES
    ])


@router.get("/llm-providers", response_model=list[LlmProviderResponse])
async def list_llm_providers(
    service: LlmConfigurationService = Depends(get_llm_configuration_service),
) -> list[LlmProviderResponse]:
    return [await _response(service, row) for row in await service.list_providers()]


@router.get("/llm-providers/{provider_id}", response_model=LlmProviderResponse)
async def get_llm_provider(
    provider_id: str,
    service: LlmConfigurationService = Depends(get_llm_configuration_service),
) -> LlmProviderResponse:
    row = await service.get_provider(_parse_provider_id(provider_id))
    if row is None:
        raise HTTPException(status_code=404, detail=f"LLM provider not found: {provider_id}")
    return await _response(service, row)


@router.post("/llm-providers", response_model=LlmProviderResponse, status_code=201)
async def create_llm_provider(
    body: LlmProviderCreate,
    service: LlmConfigurationService = Depends(get_llm_configuration_service),
) -> LlmProviderResponse:
    try:
        row = await service.create_provider(body)
    except LlmConfigurationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LlmConfigurationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _response(service, row)


@router.patch("/llm-providers/{provider_id}", response_model=LlmProviderResponse)
async def update_llm_provider(
    provider_id: str,
    body: LlmProviderUpdate,
    service: LlmConfigurationService = Depends(get_llm_configuration_service),
) -> LlmProviderResponse:
    try:
        row = await service.update_provider(_parse_provider_id(provider_id), body)
    except LlmConfigurationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LlmConfigurationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"LLM provider not found: {provider_id}")
    return await _response(service, row)


@router.delete("/llm-providers/{provider_id}", status_code=200)
async def delete_llm_provider(
    provider_id: str,
    service: LlmConfigurationService = Depends(get_llm_configuration_service),
) -> None:
    normalized_id = _parse_provider_id(provider_id)
    if await service.get_provider(normalized_id) is None:
        raise HTTPException(status_code=404, detail=f"LLM provider not found: {provider_id}")
    try:
        await service.delete_provider(normalized_id)
    except LlmConfigurationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
