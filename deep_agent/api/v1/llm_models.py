from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from deep_agent.agent.models.catalog import CatalogSource
from deep_agent.db.deps import get_llm_model_store
from deep_agent.db.ids import normalize_agent_id
from deep_agent.db.stores.llm_model import LlmModelStoreBase
from deep_agent.schemas.llm_model import LlmModelCreate, LlmModelResponse, LlmModelUpdate
from deep_agent.schemas.model_catalog import ModelCatalogItem, ModelCatalogResponse

router = APIRouter()
logger = logging.getLogger(__name__)


def _parse_model_id(model_id: str) -> str:
    try:
        return normalize_agent_id(model_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


async def _response_for_row(store: LlmModelStoreBase, row) -> LlmModelResponse:
    agent_count = await store.count_agents(row.id)
    return LlmModelResponse.from_row(row, agent_count=agent_count)


async def _refresh_auto_modalities(store: LlmModelStoreBase, model_id: str) -> None:
    from deep_agent.agent.models.modality_resolver import refresh_model_modalities

    await refresh_model_modalities(model_id, store)


async def _row_after_auto_refresh(store: LlmModelStoreBase, model_id: str):
    await _refresh_auto_modalities(store, model_id)
    return await store.get_by_id(model_id)


async def _best_effort_row_after_auto_refresh(store: LlmModelStoreBase, row):
    try:
        return await _row_after_auto_refresh(store, row.id) or row
    except Exception:
        logger.warning(
            "Model configuration %s was saved; automatic modality refresh failed and was skipped",
            row.id,
        )
        return row


@router.get("/llm-models", response_model=list[LlmModelResponse])
async def list_llm_models(
    store: LlmModelStoreBase = Depends(get_llm_model_store),
) -> list[LlmModelResponse]:
    rows = await store.list_all()
    return [await _response_for_row(store, row) for row in rows]


@router.get("/llm-models/catalog", response_model=ModelCatalogResponse)
async def list_model_catalog(
    source: Annotated[CatalogSource, Query()] = "auto",
    q: str | None = Query(None, description="Search provider, model id, or key."),
    provider: str | None = Query(None, description="Filter by provider id."),
    modality: str | None = Query(None, description="Filter by input modality, e.g. image."),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    refresh: bool = Query(False, description="Bypass in-process catalog caches."),
    store: LlmModelStoreBase = Depends(get_llm_model_store),
) -> ModelCatalogResponse:
    """Browse models.dev / bundled modality catalog for model selection."""
    from deep_agent.agent.models.catalog import (
        build_configured_preset_map,
        catalog_source_label,
        filter_catalog_entries,
        load_catalog_entries,
        lookup_configured_preset,
        paginate_catalog,
    )

    entries, resolved, provider_ids = await load_catalog_entries(
        source,
        provider=provider,
        refresh=refresh,
    )
    filtered = filter_catalog_entries(entries, q=q, provider=provider, modality=modality)
    page, total = paginate_catalog(filtered, offset=offset, limit=limit)

    preset_rows = await store.list_all()
    preset_map = build_configured_preset_map([(r.name, r.model) for r in preset_rows])

    source_label = catalog_source_label(resolved)
    if source == "online" and resolved == "bundled":
        source_label = "内置 modality_data.json（models.dev 不可用，已回退）"

    items = [
        ModelCatalogItem(
            key=e.key,
            provider=e.provider,
            model_id=e.model_id,
            provider_label=e.provider_label,
            input_modalities=e.input_modalities,
            max_context=e.max_context,
            source=e.source,
            configured_preset=lookup_configured_preset(preset_map, e),
        )
        for e in page
    ]

    return ModelCatalogResponse(
        items=items,
        total=total,
        source=resolved,
        source_label=source_label,
        offset=offset,
        limit=limit,
        providers=provider_ids,
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
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not body.input_modalities:
        row = await _best_effort_row_after_auto_refresh(store, row)
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
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"LLM model not found: {model_id}")
    if body.modalities_mode == "auto":
        row = await _best_effort_row_after_auto_refresh(store, row)
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


@router.post("/llm-models/{model_id}/refresh-metadata", response_model=LlmModelResponse)
async def refresh_model_metadata(
    model_id: str,
    store: LlmModelStoreBase = Depends(get_llm_model_store),
) -> LlmModelResponse:
    """Refresh input modalities for a model from bundled data."""
    from deep_agent.agent.models.modality_resolver import refresh_model_modalities

    normalized_id = _parse_model_id(model_id)
    row = await store.get_by_id(normalized_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"LLM model not found: {model_id}")

    await refresh_model_modalities(normalized_id, store, force=True)
    row = await store.get_by_id(normalized_id)
    return await _response_for_row(store, row)


@router.post("/llm-models/refresh-metadata")
async def refresh_all_metadata(
    store: LlmModelStoreBase = Depends(get_llm_model_store),
) -> dict[str, int]:
    """Batch refresh input modalities for all models from bundled data."""
    from deep_agent.agent.models.modality_resolver import refresh_all_model_modalities

    count = await refresh_all_model_modalities(store, force=False)
    return {"updated": count}
