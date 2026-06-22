from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.capabilities import load_and_register_mcp
from deep_agent.capabilities.registry import CapabilityRegistry
from deep_agent.core.context import AppContextDep
from deep_agent.db.deps import get_db
from deep_agent.db.repositories.mcp import McpServerRepository
from deep_agent.schemas.mcp import McpBuiltinConfig, McpServerCreate, McpServerResponse, McpServerUpdate

router = APIRouter()
_repo = McpServerRepository()


async def _reload_mcp_registry(registry: CapabilityRegistry, db: AsyncSession) -> None:
    await load_and_register_mcp(registry, db)


def _assert_builtin_row_mutable(row, body: McpServerUpdate) -> None:
    if row.transport != "builtin":
        return
    if body.transport is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Builtin MCP transport cannot be changed via API",
        )
    if body.config is not None:
        try:
            McpBuiltinConfig.model_validate(body.config)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc


@router.get("/mcp", response_model=list[McpServerResponse])
async def list_mcp_servers(db: AsyncSession = Depends(get_db)) -> list[McpServerResponse]:
    rows = await _repo.list_all(db)
    return [McpServerResponse.from_row(row) for row in rows]


@router.get("/mcp/{name}", response_model=McpServerResponse)
async def get_mcp_server(name: str, db: AsyncSession = Depends(get_db)) -> McpServerResponse:
    row = await _repo.get_by_name(db, name)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server not found: {name}")
    return McpServerResponse.from_row(row)


@router.post("/mcp", response_model=McpServerResponse, status_code=status.HTTP_201_CREATED)
async def create_mcp_server(
    ctx: AppContextDep,
    body: McpServerCreate,
    db: AsyncSession = Depends(get_db),
) -> McpServerResponse:
    existing = await _repo.get_by_name(db, body.name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"MCP server already exists: {body.name}",
        )
    row = await _repo.create(db, body)
    await _reload_mcp_registry(ctx.registry, db)
    return McpServerResponse.from_row(row)


@router.patch("/mcp/{name}", response_model=McpServerResponse)
async def update_mcp_server(
    ctx: AppContextDep,
    name: str,
    body: McpServerUpdate,
    db: AsyncSession = Depends(get_db),
) -> McpServerResponse:
    row = await _repo.get_by_name(db, name)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server not found: {name}")
    _assert_builtin_row_mutable(row, body)
    row = await _repo.update(db, row, body)
    await _reload_mcp_registry(ctx.registry, db)
    return McpServerResponse.from_row(row)


@router.delete("/mcp/{name}", status_code=status.HTTP_200_OK)
async def delete_mcp_server(
    ctx: AppContextDep,
    name: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    deleted = await _repo.delete(db, name)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server not found: {name}")
    await _reload_mcp_registry(ctx.registry, db)
