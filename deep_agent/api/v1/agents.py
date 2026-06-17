from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.api.v1.deps import get_settings
from deep_agent.core.config import validate_agent_max_requests
from deep_agent.db.deps import get_db
from deep_agent.db.ids import normalize_agent_id
from deep_agent.db.repositories.agent import AgentRepository
from deep_agent.schemas.agent import AgentCreate, AgentResponse, AgentUpdate

router = APIRouter()
_repo = AgentRepository()


def _parse_agent_id(agent_id: str) -> str:
    try:
        return normalize_agent_id(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def _check_max_requests(request: Request, max_requests: int | None) -> None:
    settings = get_settings(request)
    try:
        validate_agent_max_requests(max_requests, settings.max_requests)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/agents", response_model=list[AgentResponse])
async def list_agents(db: AsyncSession = Depends(get_db)) -> list[AgentResponse]:
    rows = await _repo.list_all(db)
    return [AgentResponse.from_row(row) for row in rows]


@router.get("/agents/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: str, db: AsyncSession = Depends(get_db)) -> AgentResponse:
    normalized_id = _parse_agent_id(agent_id)
    row = await _repo.get_by_id(db, normalized_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
    return AgentResponse.from_row(row)


@router.post("/agents", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    request: Request,
    body: AgentCreate,
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    _check_max_requests(request, body.max_requests)
    existing = await _repo.get_by_name(db, body.name)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Agent already exists: {body.name}")
    row = await _repo.create(db, body)
    return AgentResponse.from_row(row)


@router.patch("/agents/{agent_id}", response_model=AgentResponse)
async def update_agent(
    request: Request,
    agent_id: str,
    body: AgentUpdate,
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    if "max_requests" in body.model_fields_set:
        _check_max_requests(request, body.max_requests)
    normalized_id = _parse_agent_id(agent_id)
    row = await _repo.get_by_id(db, normalized_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
    if body.name is not None and body.name != row.name:
        conflict = await _repo.get_by_name(db, body.name)
        if conflict is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Agent already exists: {body.name}")
    row = await _repo.update(db, row, body)
    return AgentResponse.from_row(row)


@router.delete("/agents/{agent_id}", status_code=status.HTTP_200_OK)
async def delete_agent(agent_id: str, db: AsyncSession = Depends(get_db)) -> None:
    normalized_id = _parse_agent_id(agent_id)
    deleted = await _repo.delete(db, normalized_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
