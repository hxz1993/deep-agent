from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_db
from app.db.repositories.agent import AgentRepository
from app.schemas.agent import AgentCreate, AgentResponse, AgentUpdate

router = APIRouter()
_repo = AgentRepository()


@router.get("/agents", response_model=list[AgentResponse])
async def list_agents(db: AsyncSession = Depends(get_db)) -> list[AgentResponse]:
    rows = await _repo.list_all(db)
    return [AgentResponse.from_row(row) for row in rows]


@router.get("/agents/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: UUID, db: AsyncSession = Depends(get_db)) -> AgentResponse:
    row = await _repo.get_by_id(db, agent_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
    return AgentResponse.from_row(row)


@router.post("/agents", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(body: AgentCreate, db: AsyncSession = Depends(get_db)) -> AgentResponse:
    existing = await _repo.get_by_name(db, body.name)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Agent already exists: {body.name}")
    row = await _repo.create(db, body)
    return AgentResponse.from_row(row)


@router.patch("/agents/{agent_id}", response_model=AgentResponse)
async def update_agent(agent_id: UUID, body: AgentUpdate, db: AsyncSession = Depends(get_db)) -> AgentResponse:
    row = await _repo.get_by_id(db, agent_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
    if body.name is not None and body.name != row.name:
        conflict = await _repo.get_by_name(db, body.name)
        if conflict is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Agent already exists: {body.name}")
    row = await _repo.update(db, row, body)
    return AgentResponse.from_row(row)


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: UUID, db: AsyncSession = Depends(get_db)) -> None:
    deleted = await _repo.delete(db, agent_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent not found: {agent_id}")
