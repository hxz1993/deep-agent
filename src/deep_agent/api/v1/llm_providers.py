from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.services.codex_provider import FLOWS, CodexUnavailable, codex_enabled

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


class CodexAuthorizationStart(BaseModel):
    provider_id: str | None = None


class CodexAuthorizationVerify(BaseModel):
    provider_id: str | None = None
    callback_url: str = Field(max_length=4096)


def _codex_error(exc: CodexUnavailable) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


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
        auth_method=row.auth_method,
        auth_status=row.auth_status,
        authenticated=bool(row.credential_json) and row.auth_status == "connected" if row.auth_method == "oauth" else bool(row.api_key),
        account_hint=("••••" + str(row.credential_json.get("account_id", ""))[-4:]) if row.auth_method == "oauth" and isinstance(row.credential_json, dict) and row.credential_json.get("account_id") else None,
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
        for item in PROVIDER_TYPES if item.code != "openai_codex" or codex_enabled()
    ])


@router.post("/llm-providers/codex/authorization")
async def start_codex_authorization(
    body: CodexAuthorizationStart,
    user: RequestUserDep,
    service: LlmConfigurationService = Depends(get_llm_configuration_service),
) -> dict[str, str]:
    provider_id = _parse_provider_id(body.provider_id) if body.provider_id else None
    if provider_id:
        row = await service.get_provider(provider_id)
        if row is None or row.provider_type != "openai_codex":
            raise HTTPException(status_code=404, detail="订阅供应商不存在")
    try:
        flow_id, authorization_url = await FLOWS.start(user, provider_id)
    except CodexUnavailable as exc:
        raise _codex_error(exc) from exc
    return {"flow_id": flow_id, "authorization_url": authorization_url}


@router.post("/llm-providers/codex/authorization/{flow_id}/verify")
async def verify_codex_authorization(
    flow_id: str,
    body: CodexAuthorizationVerify,
    user: RequestUserDep,
) -> dict[str, str]:
    try:
        account_id = await FLOWS.verify(flow_id, user, _parse_provider_id(body.provider_id) if body.provider_id else None, body.callback_url)
    except CodexUnavailable as exc:
        raise _codex_error(exc) from exc
    return {"status": "verified", "account_hint": "••••" + account_id[-4:]}


@router.post("/llm-providers/codex/authorization/{flow_id}/cancel")
async def cancel_codex_authorization(
    flow_id: str,
    body: CodexAuthorizationStart,
    user: RequestUserDep,
) -> dict[str, bool]:
    try:
        await FLOWS.cancel(flow_id, user, _parse_provider_id(body.provider_id) if body.provider_id else None)
    except CodexUnavailable as exc:
        raise _codex_error(exc) from exc
    return {"cancelled": True}


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
    user: str | None = Header(default=None),
    service: LlmConfigurationService = Depends(get_llm_configuration_service),
) -> LlmProviderResponse:
    try:
        if body.provider_type == "openai_codex":
            if not body.authorization_flow_id:
                raise LlmConfigurationValidationError("请先完成订阅授权")
            row = await FLOWS.commit(body.authorization_flow_id, (user or "").strip(), None, lambda credentials: service.create_provider(body, codex_credentials=credentials))
        else:
            if body.authorization_flow_id:
                raise LlmConfigurationValidationError("API Provider 不接受订阅授权")
            row = await service.create_provider(body)
    except LlmConfigurationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LlmConfigurationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CodexUnavailable as exc:
        raise _codex_error(exc) from exc
    return await _response(service, row)


@router.patch("/llm-providers/{provider_id}", response_model=LlmProviderResponse)
async def update_llm_provider(
    provider_id: str,
    body: LlmProviderUpdate,
    user: str | None = Header(default=None),
    service: LlmConfigurationService = Depends(get_llm_configuration_service),
) -> LlmProviderResponse:
    try:
        normalized_id = _parse_provider_id(provider_id)
        if body.authorization_flow_id:
            row = await FLOWS.commit(body.authorization_flow_id, (user or "").strip(), normalized_id, lambda credentials: service.update_provider(normalized_id, body, codex_credentials=credentials))
        else:
            row = await service.update_provider(normalized_id, body)
    except LlmConfigurationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LlmConfigurationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CodexUnavailable as exc:
        raise _codex_error(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"LLM provider not found: {provider_id}")
    return await _response(service, row)


@router.post("/llm-providers/{provider_id}/disconnect", response_model=LlmProviderResponse)
async def disconnect_llm_provider(
    provider_id: str,
    service: LlmConfigurationService = Depends(get_llm_configuration_service),
) -> LlmProviderResponse:
    try:
        row = await service.disconnect_provider(_parse_provider_id(provider_id))
    except LlmConfigurationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
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
