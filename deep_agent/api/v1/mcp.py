from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from deep_agent.agent.headers import extract_forward_headers
from deep_agent.capabilities.mcp.inspection import (
    inspect_mcp_server,
    mark_mcp_inspection_stale,
)
from deep_agent.db.deps import get_mcp_store
from deep_agent.db.stores.mcp import McpServerStoreBase
from deep_agent.schemas.mcp import (
    McpBuiltinConfig,
    McpInspectionResult,
    McpServerCreate,
    McpServerResponse,
    McpServerUpdate,
    McpUrlConfig,
)

router = APIRouter()


async def _inspect_and_store(
    store: McpServerStoreBase,
    row,
    request: Request,
):
    result = await inspect_mcp_server(
        row,
        request_headers=extract_forward_headers(request),
    )
    updated = await store.update_inspection(
        row.name,
        result.inspection.model_dump(mode="json"),
    )
    return updated, result


def _assert_row_mutable(row, body: McpServerUpdate) -> None:
    if row.transport == "builtin":
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
        return
    if body.config is not None:
        try:
            McpUrlConfig.model_validate(body.config)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc


def _should_reinspect(row, body: McpServerUpdate) -> bool:
    effective_transport = body.transport or row.transport
    effective_enabled = body.enabled if body.enabled is not None else row.enabled
    return effective_transport == "url" and effective_enabled and bool(
        {"transport", "config", "enabled"} & body.model_fields_set
    )


@router.get("/mcp", response_model=list[McpServerResponse])
async def list_mcp_servers(
    store: McpServerStoreBase = Depends(get_mcp_store),
) -> list[McpServerResponse]:
    rows = await store.list_all()
    return [McpServerResponse.from_row(row) for row in rows]


@router.get("/mcp/{name}", response_model=McpServerResponse)
async def get_mcp_server(
    name: str,
    store: McpServerStoreBase = Depends(get_mcp_store),
) -> McpServerResponse:
    row = await store.get_by_name(name)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server not found: {name}")
    return McpServerResponse.from_row(row)


@router.post("/mcp", response_model=McpServerResponse, status_code=status.HTTP_201_CREATED)
async def create_mcp_server(
    body: McpServerCreate,
    request: Request,
    store: McpServerStoreBase = Depends(get_mcp_store),
) -> McpServerResponse:
    existing = await store.get_by_name(body.name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"MCP server already exists: {body.name}",
        )
    try:
        row = await store.create_unique(body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    inspected = None
    if row.transport == "url" and row.enabled:
        inspected, _ = await _inspect_and_store(store, row, request)
    return McpServerResponse.from_row(inspected or row)


@router.patch("/mcp/{name}", response_model=McpServerResponse)
async def update_mcp_server(
    name: str,
    body: McpServerUpdate,
    request: Request,
    store: McpServerStoreBase = Depends(get_mcp_store),
) -> McpServerResponse:
    row = await store.get_by_name(name)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server not found: {name}")
    _assert_row_mutable(row, body)
    updated = await store.update(name, body)
    assert updated is not None
    config_changed = bool({"transport", "config"} & body.model_fields_set)
    if config_changed:
        stale = mark_mcp_inspection_stale(row)
        updated = await store.update_inspection(
            name,
            stale.model_dump(mode="json"),
        ) or updated
    if _should_reinspect(row, body):
        updated, _ = await _inspect_and_store(store, updated, request)
        updated = updated or row
    return McpServerResponse.from_row(updated)


@router.delete("/mcp/{name}", status_code=status.HTTP_200_OK)
async def delete_mcp_server(
    name: str,
    store: McpServerStoreBase = Depends(get_mcp_store),
) -> None:
    deleted = await store.delete(name)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server not found: {name}")


@router.post("/mcp/{name}/inspect", response_model=McpInspectionResult)
async def inspect_mcp(
    name: str,
    request: Request,
    store: McpServerStoreBase = Depends(get_mcp_store),
) -> McpInspectionResult:
    row = await store.get_by_name(name)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server not found: {name}")
    _, result = await _inspect_and_store(store, row, request)
    return result
