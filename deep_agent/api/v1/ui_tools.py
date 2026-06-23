from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.capabilities.ui.builtin import builtin_ui_records
from deep_agent.capabilities.ui.registry import load_effective_ui_tools, merge_definitions
from deep_agent.capabilities.ui.types import UIToolRecord
from deep_agent.db.deps import get_db, get_ui_tool_repo, get_uow
from deep_agent.db.repositories.ui_tool import UiToolRepository
from deep_agent.schemas.ui_tools import UiToolCreate, UiToolListResponse, UiToolResponse, UiToolUpdate

router = APIRouter()
_BUILTIN_NAMES = {record.name for record in builtin_ui_records()}


@router.get("/ui-tools", response_model=UiToolListResponse)
async def list_ui_tools(
    db: AsyncSession = Depends(get_db),
    repo: UiToolRepository = Depends(get_ui_tool_repo),
    agent_id: str | None = Query(default=None),
    mcp_server: str | None = Query(default=None),
    source: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
) -> UiToolListResponse:
    rows = await repo.list_all(db, source=source, mcp_server_name=mcp_server, enabled=enabled)
    if agent_id is not None:
        rows = [row for row in rows if not row.agent_ids or agent_id in row.agent_ids]

    db_records = [UIToolRecord.from_row(row) for row in rows if row.source != "builtin"]
    merged, _ = merge_definitions(builtin_ui_records(), db_records)
    winners = {record.name: record for record in merged}

    items: list[UiToolResponse] = []
    for row in rows:
        winner = winners.get(row.name)
        shadowed_by = None
        if winner is not None and winner.source != row.source:
            shadowed_by = winner.source
        items.append(UiToolResponse.from_row(row, shadowed_by=shadowed_by))

    return UiToolListResponse(items=items, total=len(items))


@router.get("/ui-tools/{name}", response_model=UiToolResponse)
async def get_ui_tool(
    name: str,
    db: AsyncSession = Depends(get_db),
    repo: UiToolRepository = Depends(get_ui_tool_repo),
) -> UiToolResponse:
    row = await repo.get_by_name(db, name)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"UI tool not found: {name}")
    return UiToolResponse.from_row(row)


@router.post("/ui-tools", response_model=UiToolResponse, status_code=status.HTTP_201_CREATED)
async def create_ui_tool(
    body: UiToolCreate,
    db: AsyncSession = Depends(get_uow),
    repo: UiToolRepository = Depends(get_ui_tool_repo),
) -> UiToolResponse:
    if body.name in _BUILTIN_NAMES and not body.override:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Builtin tool name is reserved: {body.name}",
        )
    existing = await repo.get_by_name(db, body.name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"UI tool already exists: {body.name}",
        )
    row = await repo.create_manual(db, body)
    return UiToolResponse.from_row(row)


@router.patch("/ui-tools/{name}", response_model=UiToolResponse)
async def update_ui_tool(
    name: str,
    body: UiToolUpdate,
    db: AsyncSession = Depends(get_uow),
    repo: UiToolRepository = Depends(get_ui_tool_repo),
    force: bool = Query(default=False),
) -> UiToolResponse:
    row = await repo.get_by_name(db, name)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"UI tool not found: {name}")
    if row.source == "builtin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Builtin UI tools cannot be modified")
    if row.source == "mcp" and not force:
        allowed = {"enabled", "agent_ids", "override"}
        updates = body.model_dump(exclude_unset=True)
        if set(updates) - allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MCP-discovered tools only allow enabled, agent_ids, and override unless force=true",
            )
    row = await repo.update(db, row, body)
    return UiToolResponse.from_row(row)


@router.delete("/ui-tools/{name}", status_code=status.HTTP_200_OK)
async def delete_ui_tool(
    name: str,
    db: AsyncSession = Depends(get_uow),
    repo: UiToolRepository = Depends(get_ui_tool_repo),
) -> None:
    row = await repo.get_by_name(db, name)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"UI tool not found: {name}")
    if row.source == "builtin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Builtin UI tools cannot be deleted")
    await repo.delete(db, name)
