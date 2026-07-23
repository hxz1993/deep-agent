from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from deep_agent.agent.headers import extract_forward_headers
from deep_agent.capabilities.mcp.resources import probe_mcp_resource_capabilities
from deep_agent.db.deps import get_mcp_store
from deep_agent.db.stores.mcp import McpServerStoreBase
from deep_agent.schemas.mcp import (
    McpBuiltinConfig,
    McpResourceCapabilities,
    McpServerCreate,
    McpServerResponse,
    McpServerUpdate,
    McpUrlConfig,
)

router = APIRouter()


async def _probe_and_store_resources(
    store: McpServerStoreBase,
    row,
    request: Request,
):
    capabilities = await probe_mcp_resource_capabilities(
        row,
        request_headers=extract_forward_headers(request),
    )
    return await store.update_resource_capabilities(
        row.name,
        capabilities.model_dump(mode="json"),
    )


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


def _should_reprobe_resources(row, body: McpServerUpdate) -> bool:
    effective_transport = body.transport or row.transport
    return effective_transport == "url" and bool(
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
    probed = None
    if row.transport == "url":
        probed = await _probe_and_store_resources(store, row, request)
    return McpServerResponse.from_row(probed or row)


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
    if _should_reprobe_resources(row, body):
        updated = await _probe_and_store_resources(store, updated, request) or updated
    return McpServerResponse.from_row(updated)


@router.delete("/mcp/{name}", status_code=status.HTTP_200_OK)
async def delete_mcp_server(
    name: str,
    store: McpServerStoreBase = Depends(get_mcp_store),
) -> None:
    deleted = await store.delete(name)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server not found: {name}")


@router.post("/mcp/{name}/resources/probe", response_model=McpResourceCapabilities)
async def probe_mcp_resources(
    name: str,
    request: Request,
    store: McpServerStoreBase = Depends(get_mcp_store),
) -> McpResourceCapabilities:
    row = await store.get_by_name(name)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server not found: {name}")
    updated = await _probe_and_store_resources(store, row, request)
    return McpResourceCapabilities.from_value(
        (updated or row).resource_capabilities,
    )
