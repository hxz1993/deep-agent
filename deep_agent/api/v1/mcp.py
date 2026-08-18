from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request, status

from deep_agent.agent.headers import extract_forward_headers
from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.capabilities.mcp.inspection import (
    inspect_mcp_server,
    mark_mcp_inspection_stale,
)
from deep_agent.db.deps import get_agent_store, get_mcp_store
from deep_agent.db.stores.agent import AgentStoreBase
from deep_agent.db.stores.mcp import McpServerStoreBase
from deep_agent.schemas.mcp import (
    McpBuiltinConfig,
    McpInspectionResult,
    McpServerCreate,
    McpServerRenameRequest,
    McpServerRenameResponse,
    McpServerResponse,
    McpServerUpdate,
    normalize_mcp_url_config,
)

router = APIRouter(dependencies=CONSOLE_ADMIN_DEPENDENCIES)


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
        result.inspection.to_storage_value(),
    )
    return updated, result


def _validate_row_update(row, body: McpServerUpdate) -> McpServerUpdate:
    if row.transport == "stdio":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="stdio MCP 已停止支持；历史配置只能查看或删除",
        )
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
        return body
    if body.config is not None:
        try:
            normalized = normalize_mcp_url_config(body.config)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        body = body.model_copy(update={"config": normalized})
    return body


def _candidate_row(row, body: McpServerUpdate):
    return SimpleNamespace(
        name=row.name,
        transport=body.transport or row.transport,
        config=body.config if body.config is not None else row.config,
        description=body.description if "description" in body.model_fields_set else row.description,
        enabled=body.enabled if body.enabled is not None else row.enabled,
        inspection=getattr(row, "inspection", None),
    )


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
    rows = await store.list_for_admin()
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
    inspection = None
    if body.transport == "url" and body.enabled:
        candidate = SimpleNamespace(
            name=body.name,
            transport=body.transport,
            config=body.config,
            description=body.description,
            enabled=body.enabled,
            inspection=None,
        )
        result = await inspect_mcp_server(
            candidate,
            request_headers=extract_forward_headers(request),
        )
        inspection = result.inspection.to_storage_value()
    try:
        row = await store.create_unique(body, inspection=inspection)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return McpServerResponse.from_row(row)


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
    body = _validate_row_update(row, body)
    candidate = _candidate_row(row, body)
    config_changed = bool({"transport", "config"} & body.model_fields_set)
    if config_changed:
        candidate.inspection = mark_mcp_inspection_stale(row).to_storage_value()
    inspection = candidate.inspection
    if _should_reinspect(row, body):
        result = await inspect_mcp_server(
            candidate,
            request_headers=extract_forward_headers(request),
        )
        inspection = result.inspection.to_storage_value()
    updated = await store.update(name, body, inspection=inspection)
    assert updated is not None
    return McpServerResponse.from_row(updated)


@router.post("/mcp/{name}/rename", response_model=McpServerRenameResponse)
async def rename_mcp_server(
    name: str,
    body: McpServerRenameRequest,
    store: McpServerStoreBase = Depends(get_mcp_store),
) -> McpServerRenameResponse:
    try:
        result = await store.rename_with_agent_references(name, body.new_name)
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server not found: {name}",
        )
    return McpServerRenameResponse(
        old_name=name,
        new_name=result.row.name,
        updated_agents=result.updated_agent_names,
        mcp=McpServerResponse.from_row(result.row),
    )


@router.delete("/mcp/{name}", status_code=status.HTTP_200_OK)
async def delete_mcp_server(
    name: str,
    store: McpServerStoreBase = Depends(get_mcp_store),
    agent_store: AgentStoreBase = Depends(get_agent_store),
) -> None:
    row = await store.get_by_name(name)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server not found: {name}")
    if row.transport == "builtin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="内建 MCP 由程序管理，不能通过 API 删除",
        )
    referenced_by = await agent_store.list_referencing_capability(f"mcp:{name}")
    if referenced_by:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "MCP 正在被 Agent 引用，不能删除",
                "resource": name,
                "referenced_by": [agent.name for agent in referenced_by],
            },
        )
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
