from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status

from deep_agent.models.catalog import (
    CatalogValidationError,
    build_catalog_page,
    export_models_dev_catalog,
    normalize_models_dev_bundle,
)
from deep_agent.models.models_dev import (
    ModelsDevCatalogError,
    ModelsDevFetchError,
    fetch_models_dev_bundle,
    parse_models_dev_catalog,
)
from deep_agent.models.connectivity import probe_model
from deep_agent.models.provider_registry import model_unavailable_reason
from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.db.deps import (
    get_llm_configuration_service,
    get_llm_model_store,
    get_model_catalog_store,
)
from deep_agent.db.ids import normalize_agent_id, normalize_provider_id
from deep_agent.db.stores.llm_model import (
    LlmModelStoreBase,
    LlmModelValidationError,
)
from deep_agent.db.stores.model_catalog import ModelCatalogStoreBase
from deep_agent.schemas.llm_model import LlmModelCreate, LlmModelResponse, LlmModelUpdate, LlmModelTestResponse
from deep_agent.schemas.model_catalog import (
    ModelCatalogRefreshResponse,
    ModelCatalogResponse,
)
from deep_agent.services.llm_configuration import (
    LlmConfigurationConflictError,
    LlmConfigurationService,
    LlmConfigurationValidationError,
)

router = APIRouter(dependencies=CONSOLE_ADMIN_DEPENDENCIES)
_catalog_refresh_lock = asyncio.Lock()
_MAX_CATALOG_IMPORT_BYTES = 16 * 1024 * 1024
_model_test_slots = asyncio.Semaphore(2)


def _parse_model_id(model_id: str) -> str:
    try:
        return normalize_agent_id(model_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def _parse_provider_id(provider_id: str) -> str:
    try:
        return normalize_provider_id(provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


async def _response_for_row(
    store: LlmModelStoreBase,
    service: LlmConfigurationService,
    row,
    *,
    provider=None,
    agent_count: int | None = None,
) -> LlmModelResponse:
    if agent_count is None:
        agent_count = await store.count_agents(row.id)
    if provider is None:
        provider = await service.get_provider(row.provider_id)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"LLM provider not found for model: {row.id}",
        )
    return LlmModelResponse.from_row(
        row,
        provider=provider,
        agent_count=agent_count,
    )


@router.get("/llm-models", response_model=list[LlmModelResponse])
async def list_llm_models(
    provider_id: str | None = Query(None),
    selectable_only: bool = Query(
        True,
        description="Return only models currently available for Agent or request selection.",
    ),
    store: LlmModelStoreBase = Depends(get_llm_model_store),
    service: LlmConfigurationService = Depends(get_llm_configuration_service),
) -> list[LlmModelResponse]:
    rows = await store.list_all()
    if provider_id is not None:
        normalized_provider_id = _parse_provider_id(provider_id)
        rows = [row for row in rows if row.provider_id == normalized_provider_id]
    providers = {row.id: row for row in await service.list_providers()}
    agent_counts = await store.count_agents_by_model()
    responses: list[LlmModelResponse] = []
    for row in rows:
        provider = providers.get(row.provider_id)
        if provider is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"LLM provider not found for model: {row.id}",
            )
        response = await _response_for_row(
            store,
            service,
            row,
            provider=provider,
            agent_count=agent_counts.get(row.id, 0),
        )
        if not selectable_only or response.selectable:
            responses.append(response)
    return responses


@router.get("/llm-models/catalog", response_model=ModelCatalogResponse)
async def list_model_catalog(
    view: Literal["model", "provider"] = Query("model"),
    q: str | None = Query(None, description="Search Lab, model, or Provider."),
    lab_id: str | None = Query(None, description="Filter by Lab id."),
    provider_id: str | None = Query(None, description="Filter by Provider id."),
    modality: str | None = Query(None, description="Filter by input modality."),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    catalog_store: ModelCatalogStoreBase = Depends(get_model_catalog_store),
    llm_store: LlmModelStoreBase = Depends(get_llm_model_store),
    service: LlmConfigurationService = Depends(get_llm_configuration_service),
) -> ModelCatalogResponse:
    rows = await catalog_store.list_all()
    llm_models = await llm_store.list_all()
    llm_providers = await service.list_providers()
    return ModelCatalogResponse.model_validate(
        build_catalog_page(
            rows,
            llm_models,
            llm_providers,
            view=view,
            q=q,
            lab_id=lab_id,
            provider_id=provider_id,
            modality=modality,
            offset=offset,
            limit=limit,
        )
    )


@router.get("/llm-models/catalog/export")
async def export_model_catalog(
    catalog_store: ModelCatalogStoreBase = Depends(get_model_catalog_store),
) -> Response:
    rows = await catalog_store.list_all()
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="模型目录为空，请先在线更新或导入JSON",
        )
    payload = export_models_dev_catalog(rows)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="models-dev-catalog-{timestamp}.json"'
            )
        },
    )


@router.post(
    "/llm-models/catalog/refresh",
    response_model=ModelCatalogRefreshResponse,
)
async def refresh_model_catalog(
    catalog_store: ModelCatalogStoreBase = Depends(get_model_catalog_store),
) -> ModelCatalogRefreshResponse:
    if _catalog_refresh_lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="模型目录正在更新，请稍后重试",
        )

    async with _catalog_refresh_lock:
        try:
            bundle = await fetch_models_dev_bundle()
            rows, stats = normalize_models_dev_bundle(
                bundle.models,
                bundle.providers,
                bundle.labs,
            )
            await catalog_store.replace_all(rows)
        except ModelsDevFetchError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc
        except CatalogValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"models.dev 目录校验失败：{exc}",
            ) from exc

    return ModelCatalogRefreshResponse(
        rows=stats.row_count,
        models=stats.model_count,
        providers=stats.provider_count,
        labs=stats.lab_count,
        refreshed_at=stats.refreshed_at,
    )


@router.post(
    "/llm-models/catalog/import",
    response_model=ModelCatalogRefreshResponse,
)
async def import_model_catalog(
    file: UploadFile = File(...),
    catalog_store: ModelCatalogStoreBase = Depends(get_model_catalog_store),
) -> ModelCatalogRefreshResponse:
    if _catalog_refresh_lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="模型目录正在更新，请稍后重试",
        )
    filename = (file.filename or "").strip()
    if not filename.lower().endswith(".json"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="请选择models.dev官方catalog.json文件",
        )

    async with _catalog_refresh_lock:
        try:
            raw = await file.read(_MAX_CATALOG_IMPORT_BYTES + 1)
            if len(raw) > _MAX_CATALOG_IMPORT_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="模型目录JSON不能超过16 MiB",
                )
            bundle = parse_models_dev_catalog(raw)
            rows, stats = normalize_models_dev_bundle(
                bundle.models,
                bundle.providers,
                bundle.labs,
            )
            await catalog_store.replace_all(rows)
        except ModelsDevCatalogError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        except CatalogValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"models.dev 目录校验失败：{exc}",
            ) from exc
        finally:
            await file.close()

    return ModelCatalogRefreshResponse(
        rows=stats.row_count,
        models=stats.model_count,
        providers=stats.provider_count,
        labs=stats.lab_count,
        refreshed_at=stats.refreshed_at,
    )


@router.post("/llm-models/{model_id}/test", response_model=LlmModelTestResponse)
async def test_llm_model(
    model_id: str,
    provider_id: str = Query(...),
    store: LlmModelStoreBase = Depends(get_llm_model_store),
    service: LlmConfigurationService = Depends(get_llm_configuration_service),
) -> LlmModelTestResponse:
    row = await store.get_by_id(_parse_model_id(model_id))
    if row is None:
        raise HTTPException(status_code=404, detail="模型不存在，请刷新列表")
    if row.provider_id != _parse_provider_id(provider_id):
        raise HTTPException(status_code=409, detail="模型所属 Provider 已变化，请刷新列表")
    provider = await service.get_provider(row.provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider 不存在，请刷新列表")
    reason = model_unavailable_reason(row, provider)
    if reason:
        messages = {
            "model_disabled": "模型已停用，不参与测试",
            "model_wrong_purpose": "该模型不是对话用途",
            "provider_disabled": "订阅 Provider 功能未开启",
            "provider_unconfigured": "Provider 尚未配置凭据，请先完成授权或填写 API Key",
            "provider_invalid": "Provider 配置无效，请检查类型和地址",
        }
        raise HTTPException(status_code=409, detail=messages[reason])
    # Reject excess checks instead of leaving an unbounded queue of billable work.
    if _model_test_slots.locked():
        raise HTTPException(status_code=429, detail="模型测试正在进行，请稍后再试")
    async with _model_test_slots:
        return await probe_model(row, provider)


@router.get("/llm-models/{model_id}", response_model=LlmModelResponse)
async def get_llm_model(
    model_id: str,
    store: LlmModelStoreBase = Depends(get_llm_model_store),
    service: LlmConfigurationService = Depends(get_llm_configuration_service),
) -> LlmModelResponse:
    normalized_id = _parse_model_id(model_id)
    row = await store.get_by_id(normalized_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"LLM model not found: {model_id}")
    return await _response_for_row(store, service, row)


@router.post("/llm-models", response_model=LlmModelResponse, status_code=status.HTTP_201_CREATED)
async def create_llm_model(
    body: LlmModelCreate,
    store: LlmModelStoreBase = Depends(get_llm_model_store),
    service: LlmConfigurationService = Depends(get_llm_configuration_service),
) -> LlmModelResponse:
    try:
        row = await service.create_model(body)
    except LlmConfigurationConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except LlmConfigurationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return await _response_for_row(store, service, row)


@router.patch("/llm-models/{model_id}", response_model=LlmModelResponse)
async def update_llm_model(
    model_id: str,
    body: LlmModelUpdate,
    store: LlmModelStoreBase = Depends(get_llm_model_store),
    service: LlmConfigurationService = Depends(get_llm_configuration_service),
) -> LlmModelResponse:
    normalized_id = _parse_model_id(model_id)
    try:
        row = await service.update_model(normalized_id, body)
    except LlmConfigurationConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (LlmModelValidationError, LlmConfigurationValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"LLM model not found: {model_id}")
    return await _response_for_row(store, service, row)


@router.delete("/llm-models/{model_id}", status_code=status.HTTP_200_OK)
async def delete_llm_model(
    model_id: str,
    service: LlmConfigurationService = Depends(get_llm_configuration_service),
) -> None:
    normalized_id = _parse_model_id(model_id)
    try:
        deleted = await service.delete_model(normalized_id)
    except LlmConfigurationConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"LLM model not found: {model_id}")
