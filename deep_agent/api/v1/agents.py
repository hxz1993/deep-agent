from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.agent.limits import validate_agent_max_requests
from deep_agent.db.deps import get_agent_repo, get_db, get_uow
from deep_agent.db.ids import normalize_agent_id
from deep_agent.db.repositories.agent import AgentRepository
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
    db: AsyncSession = Depends(get_db),
    repo: AgentRepository = Depends(get_agent_repo),
) -> list[AgentResponse]:
    rows = await repo.list_all(db)
    return [AgentResponse.from_row(row) for row in rows]


@router.get("/agents/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    repo: AgentRepository = Depends(get_agent_repo),
) -> AgentResponse:
    normalized_id = _parse_agent_id(agent_id)
    row = await repo.get_by_id(db, normalized_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
    return AgentResponse.from_row(row)


@router.post("/agents", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreate,
    db: AsyncSession = Depends(get_uow),
    repo: AgentRepository = Depends(get_agent_repo),
) -> AgentResponse:
    _check_max_requests(body.max_requests)
    existing = await repo.get_by_name(db, body.name)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Agent already exists: {body.name}")
    row = await repo.create(db, body)
    return AgentResponse.from_row(row)


@router.patch("/agents/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    body: AgentUpdate,
    db: AsyncSession = Depends(get_uow),
    repo: AgentRepository = Depends(get_agent_repo),
) -> AgentResponse:
    if "max_requests" in body.model_fields_set:
        _check_max_requests(body.max_requests)
    normalized_id = _parse_agent_id(agent_id)
    row = await repo.get_by_id(db, normalized_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
    if body.name is not None and body.name != row.name:
        conflict = await repo.get_by_name(db, body.name)
        if conflict is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Agent already exists: {body.name}")
    row = await repo.update(db, row, body)
    return AgentResponse.from_row(row)


@router.delete("/agents/{agent_id}", status_code=status.HTTP_200_OK)
async def delete_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_uow),
    repo: AgentRepository = Depends(get_agent_repo),
) -> None:
    normalized_id = _parse_agent_id(agent_id)
    deleted = await repo.delete(db, normalized_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
