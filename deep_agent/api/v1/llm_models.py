from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status

from deep_agent.agent.models.catalog import (
    CatalogValidationError,
    build_catalog_page,
    export_models_dev_catalog,
    normalize_models_dev_bundle,
)
from deep_agent.agent.models.models_dev import (
    ModelsDevCatalogError,
    ModelsDevFetchError,
    fetch_models_dev_bundle,
    parse_models_dev_catalog,
)
from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.db.deps import get_llm_model_store, get_model_catalog_store
from deep_agent.db.ids import normalize_agent_id
from deep_agent.db.stores.llm_model import (
    LlmModelConflictError,
    LlmModelStoreBase,
    LlmModelValidationError,
)
from deep_agent.db.stores.model_catalog import ModelCatalogStoreBase
from deep_agent.schemas.llm_model import LlmModelCreate, LlmModelResponse, LlmModelUpdate
from deep_agent.schemas.model_catalog import (
    ModelCatalogRefreshResponse,
    ModelCatalogResponse,
)

router = APIRouter(dependencies=CONSOLE_ADMIN_DEPENDENCIES)
_catalog_refresh_lock = asyncio.Lock()
_MAX_CATALOG_IMPORT_BYTES = 16 * 1024 * 1024


def _parse_model_id(model_id: str) -> str:
    try:
        return normalize_agent_id(model_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


async def _response_for_row(store: LlmModelStoreBase, row) -> LlmModelResponse:
    agent_count = await store.count_agents(row.id)
    return LlmModelResponse.from_row(row, agent_count=agent_count)


@router.get("/llm-models", response_model=list[LlmModelResponse])
async def list_llm_models(
    store: LlmModelStoreBase = Depends(get_llm_model_store),
) -> list[LlmModelResponse]:
    rows = await store.list_all()
    return [await _response_for_row(store, row) for row in rows]


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
) -> ModelCatalogResponse:
    rows = await catalog_store.list_all()
    llm_models = await llm_store.list_all()
    return ModelCatalogResponse.model_validate(
        build_catalog_page(
            rows,
            llm_models,
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


@router.get("/llm-models/{model_id}", response_model=LlmModelResponse)
async def get_llm_model(
    model_id: str,
    store: LlmModelStoreBase = Depends(get_llm_model_store),
) -> LlmModelResponse:
    normalized_id = _parse_model_id(model_id)
    row = await store.get_by_id(normalized_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"LLM model not found: {model_id}")
    return await _response_for_row(store, row)


@router.post("/llm-models", response_model=LlmModelResponse, status_code=status.HTTP_201_CREATED)
async def create_llm_model(
    body: LlmModelCreate,
    store: LlmModelStoreBase = Depends(get_llm_model_store),
) -> LlmModelResponse:
    existing = await store.get_by_name(body.name)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"LLM model already exists: {body.name}")
    try:
        row = await store.create_unique(body)
    except LlmModelConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return await _response_for_row(store, row)


@router.patch("/llm-models/{model_id}", response_model=LlmModelResponse)
async def update_llm_model(
    model_id: str,
    body: LlmModelUpdate,
    store: LlmModelStoreBase = Depends(get_llm_model_store),
) -> LlmModelResponse:
    normalized_id = _parse_model_id(model_id)
    try:
        row = await store.update_unique(normalized_id, body)
    except LlmModelConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except LlmModelValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"LLM model not found: {model_id}")
    return await _response_for_row(store, row)


@router.delete("/llm-models/{model_id}", status_code=status.HTTP_200_OK)
async def delete_llm_model(
    model_id: str,
    store: LlmModelStoreBase = Depends(get_llm_model_store),
) -> None:
    normalized_id = _parse_model_id(model_id)
    row = await store.get_by_id(normalized_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"LLM model not found: {model_id}")
    agent_count = await store.count_agents(normalized_id)
    if agent_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"LLM model is referenced by {agent_count} agent(s)",
        )
    await store.delete(normalized_id)
