from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from deep_agent.db.deps import get_llm_model_store
from deep_agent.db.ids import normalize_agent_id
from deep_agent.db.stores.llm_model import LlmModelStoreBase
from deep_agent.schemas.llm_model import LlmModelCreate, LlmModelResponse, LlmModelUpdate

router = APIRouter()


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
