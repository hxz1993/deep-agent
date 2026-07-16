from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from deep_agent.capabilities.mcp.ui_discovery import discover_and_persist_ui_tools
from deep_agent.db.deps import get_mcp_store, get_ui_tool_store
from deep_agent.db.stores.mcp import McpServerStoreBase
from deep_agent.db.stores.ui_tool import UiToolStoreBase
from deep_agent.schemas.mcp import McpBuiltinConfig, McpServerCreate, McpServerResponse, McpServerUpdate, McpUrlConfig
from deep_agent.schemas.ui_tools import UiToolsRefreshResponse

router = APIRouter()


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


# ponytail: keep old name for call sites
_assert_builtin_row_mutable = _assert_row_mutable


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
    return McpServerResponse.from_row(row)


@router.patch("/mcp/{name}", response_model=McpServerResponse)
async def update_mcp_server(
    name: str,
    body: McpServerUpdate,
    store: McpServerStoreBase = Depends(get_mcp_store),
) -> McpServerResponse:
    row = await store.get_by_name(name)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server not found: {name}")
    _assert_builtin_row_mutable(row, body)
    updated = await store.update(name, body)
    assert updated is not None
    return McpServerResponse.from_row(updated)


@router.delete("/mcp/{name}", status_code=status.HTTP_200_OK)
async def delete_mcp_server(
    name: str,
    store: McpServerStoreBase = Depends(get_mcp_store),
) -> None:
    deleted = await store.delete(name)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server not found: {name}")


@router.post("/mcp/{name}/ui-tools/refresh", response_model=UiToolsRefreshResponse)
async def refresh_mcp_ui_tools(
    name: str,
    mcp_store: McpServerStoreBase = Depends(get_mcp_store),
    ui_tool_store: UiToolStoreBase = Depends(get_ui_tool_store),
) -> UiToolsRefreshResponse:
    row = await mcp_store.get_by_name(name)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server not found: {name}")
    return await discover_and_persist_ui_tools(ui_tool_store, row, use_cache_on_error=False)
