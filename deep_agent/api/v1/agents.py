from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from deep_agent.agent.limits import validate_agent_max_requests
from deep_agent.core.context import AppContextDep
from deep_agent.db.deps import get_agent_store
from deep_agent.db.ids import normalize_agent_id
from deep_agent.db.stores.agent import AgentStoreBase
from deep_agent.options import OPTION
from deep_agent.schemas.agent import AgentCreate, AgentResponse, AgentUpdate

router = APIRouter()


def _parse_agent_id(agent_id: str) -> str:
    try:
        return normalize_agent_id(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def _check_max_requests(max_requests: int | None) -> None:
    try:
        validate_agent_max_requests(max_requests, OPTION.agent.max_requests)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/agents", response_model=list[AgentResponse])
async def list_agents(
    store: AgentStoreBase = Depends(get_agent_store),
) -> list[AgentResponse]:
    rows = await store.list_all()
    return [AgentResponse.from_row(row) for row in rows]


@router.get("/agents/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    store: AgentStoreBase = Depends(get_agent_store),
) -> AgentResponse:
    normalized_id = _parse_agent_id(agent_id)
    row = await store.get_by_id(normalized_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
    return AgentResponse.from_row(row)


@router.post("/agents", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreate,
    store: AgentStoreBase = Depends(get_agent_store),
) -> AgentResponse:
    _check_max_requests(body.max_requests)
    existing = await store.get_by_name(body.name)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Agent already exists: {body.name}")
    try:
        row = await store.create_unique(body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return AgentResponse.from_row(row)


@router.patch("/agents/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    body: AgentUpdate,
    store: AgentStoreBase = Depends(get_agent_store),
) -> AgentResponse:
    if "max_requests" in body.model_fields_set:
        _check_max_requests(body.max_requests)
    normalized_id = _parse_agent_id(agent_id)
    try:
        row = await store.update_unique(normalized_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
    return AgentResponse.from_row(row)


@router.delete("/agents/{agent_id}", status_code=status.HTTP_200_OK)
async def delete_agent(
    agent_id: str,
    store: AgentStoreBase = Depends(get_agent_store),
) -> None:
    normalized_id = _parse_agent_id(agent_id)
    deleted = await store.delete(normalized_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
