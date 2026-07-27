from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from deep_agent.core.context import AppContextDep
from deep_agent.db.deps import get_agent_store, get_llm_model_store
from deep_agent.db.ids import normalize_agent_id
from deep_agent.db.stores.agent import AgentStoreBase
from deep_agent.db.stores.llm_model import LlmModelStoreBase
from deep_agent.schemas.agent import AgentCreate, AgentResponse, AgentUpdate

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


@router.get("/agents", response_model=list[AgentResponse])
async def list_agents(
    store: AgentStoreBase = Depends(get_agent_store),
) -> list[AgentResponse]:
    rows = await store.list_all()
    return [_agent_response(row) for row in rows]


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


@router.post("/agents", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreate,
    store: AgentStoreBase = Depends(get_agent_store),
    model_store: LlmModelStoreBase = Depends(get_llm_model_store),
) -> AgentResponse:
    normalized_model_id = await _ensure_model_exists(model_store, body.model_id)
    body = body.model_copy(update={"model_id": normalized_model_id})
    existing = await store.get_by_name(body.name)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Agent already exists: {body.name}")
    try:
        row = await store.create_unique(body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _agent_response(row)


@router.patch("/agents/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    body: AgentUpdate,
    store: AgentStoreBase = Depends(get_agent_store),
    model_store: LlmModelStoreBase = Depends(get_llm_model_store),
) -> AgentResponse:
    if body.model_id is not None:
        normalized_model_id = await _ensure_model_exists(model_store, body.model_id)
        body = body.model_copy(update={"model_id": normalized_model_id})
    normalized_id = _parse_agent_id(agent_id)
    try:
        row = await store.update_unique(normalized_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
    return _agent_response(row)


@router.delete("/agents/{agent_id}", status_code=status.HTTP_200_OK)
async def delete_agent(
    agent_id: str,
    store: AgentStoreBase = Depends(get_agent_store),
) -> None:
    normalized_id = _parse_agent_id(agent_id)
    deleted = await store.delete(normalized_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
