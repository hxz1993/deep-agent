from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request, status

from deep_agent.agent.headers import extract_forward_headers
from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.api.v1.request_identity import RequestUserDep, require_request_user
from deep_agent.capabilities.mcp.inspection import (
    inspect_mcp_server,
    mark_mcp_inspection_stale,
)
from deep_agent.db.deps import get_agent_store, get_mcp_store
from deep_agent.db.reference_integrity import ReferenceConflict
from deep_agent.db.stores.agent import AgentStoreBase
from deep_agent.db.stores.mcp import McpServerStoreBase
from deep_agent.mcp_request_context import resolve_mcp_request_context_mode
from deep_agent.security.console_admin import ConsoleAdminAuthenticationError, forwarded_platform_auth_headers
from deep_agent.services.integration_app_access import IntegrationAppAccessError, IntegrationAppDirectory, ManagedApp
from deep_agent.services.mcp_bound_app import bound_login_headers, bound_mcp_target
from deep_agent.services.scope_names import resolve_scope_directory
from deep_agent.schemas.mcp import (
    McpBuiltinConfig,
    McpCatalogResponse,
    McpCatalogResources,
    McpCatalogTools,
    McpInspection,
    McpInspectionResult,
    McpServerCreate,
    McpServerRenameRequest,
    McpServerRenameResponse,
    McpServerResponse,
    McpServerUpdate,
    normalize_mcp_url_config,
)

router = APIRouter(dependencies=CONSOLE_ADMIN_DEPENDENCIES)
_RESERVED_MCP_NAME = "bound-apps"


def _bound_directory(request: Request, user: str) -> tuple[IntegrationAppDirectory, dict[str, str]]:
    try:
        login = bound_login_headers(user, forwarded_platform_auth_headers(request, user))
    except ConsoleAdminAuthenticationError as exc:
        raise IntegrationAppAccessError(401, "平台身份凭据无效。") from exc
    return IntegrationAppDirectory(request, user), login


async def _validate_bound_configs(request: Request, configs: list[dict]) -> list[tuple[dict, dict]]:
    if not configs:
        return []
    user = require_request_user(request.headers.get("user"))
    try:
        directory, login = _bound_directory(request, user)
        apps = {}
        targets = [bound_mcp_target(config) for config in configs]
        for app_id, space_id in set(targets):
            app = await directory.verify(app_id)
            if (app.app_id, app.space_id) != (app_id, space_id):
                raise IntegrationAppAccessError(403, "无法验证绑定应用的归属。")
            apps[app_id] = app
        names = await resolve_scope_directory(request, user)
        return [
            (
                {**config, "request_context": {
                    "mode": "bound_app",
                    "target_app_id": app_id,
                    "target_space_id": space_id,
                    "target_app_name": apps[app_id].app_name,
                    "target_space_name": names.spaces.get(space_id) or space_id,
                }},
                {**login, "app": app_id, "space": space_id},
            )
            for config, (app_id, space_id) in zip(configs, targets, strict=True)
        ]
    except IntegrationAppAccessError as exc:
        raise HTTPException(exc.status, detail=str(exc)) from exc


async def validate_bound_mcp_import(request: Request, rows) -> None:
    bound = [row for row in rows if resolve_mcp_request_context_mode(row["transport"], row["config"]) == "bound_app"]
    validated = await _validate_bound_configs(request, [row["config"] for row in bound])
    for row, (config, _) in zip(bound, validated, strict=True):
        row["config"] = config


async def _inspect_row(row, request: Request):
    if resolve_mcp_request_context_mode(row.transport, row.config) == "bound_app":
        (_, verified_headers), = await _validate_bound_configs(request, [row.config])
        return await inspect_mcp_server(row, verified_bound_headers=verified_headers)
    return await inspect_mcp_server(row, request_headers=extract_forward_headers(request))


async def _inspect_and_store(
    store: McpServerStoreBase,
    row,
    request: Request,
) -> McpInspectionResult:
    result = await _inspect_row(row, request)
    try:
        updated = await store.update_inspection(
            row.name, result.inspection.to_storage_value(), expected=row,
        )
    except ReferenceConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"MCP server not found: {row.name}")
    return result


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
        inspection=getattr(row, "inspection", None),
    )


def _bound_for_save(candidate) -> bool:
    if resolve_mcp_request_context_mode(candidate.transport, candidate.config) != "bound_app":
        return False
    try:
        bound_mcp_target(candidate.config)
    except IntegrationAppAccessError as exc:
        raise HTTPException(exc.status, detail=str(exc)) from exc
    return True


def _should_reinspect(row, body: McpServerUpdate) -> bool:
    effective_transport = body.transport or row.transport
    return effective_transport == "url" and bool(
        {"transport", "config"} & body.model_fields_set
    )


@router.get("/mcp", response_model=list[McpServerResponse])
async def list_mcp_servers(
    store: McpServerStoreBase = Depends(get_mcp_store),
) -> list[McpServerResponse]:
    rows = await store.list_for_admin()
    return [McpServerResponse.from_row(row) for row in rows]


@router.get("/mcp/bound-apps", response_model=list[ManagedApp])
async def list_bound_apps(request: Request, user: RequestUserDep) -> list[ManagedApp]:
    try:
        directory, _ = _bound_directory(request, user)
        return await directory.available_apps()
    except IntegrationAppAccessError as exc:
        raise HTTPException(exc.status, detail=str(exc)) from exc


@router.get("/mcp/{name}", response_model=McpServerResponse)
async def get_mcp_server(
    name: str,
    store: McpServerStoreBase = Depends(get_mcp_store),
) -> McpServerResponse:
    row = await store.get_by_name(name)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MCP server not found: {name}")
    return McpServerResponse.from_row(row)


@router.get("/mcp/{name}/catalog", response_model=McpCatalogResponse)
async def get_mcp_catalog(
    name: str,
    store: McpServerStoreBase = Depends(get_mcp_store),
) -> McpCatalogResponse:
    row = await store.get_by_name(name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"MCP server not found: {name}")
    inspection = McpInspection.from_value(row.inspection, transport=row.transport)
    tools = inspection.tools
    return McpCatalogResponse(
        name=row.name,
        inspection_status=inspection.status,
        checked_at=inspection.checked_at,
        resources=McpCatalogResources(
            count=inspection.resources.count,
            snapshot_status=("missing" if inspection.resources.definitions is None else
                             "stale" if inspection.status in {"stale", "unreachable"} or inspection.resources.status == "error" else "ready"),
            definitions=inspection.resources.definitions or [],
        ),
        tools=McpCatalogTools(
            status=tools.status,
            count=tools.count,
            snapshot_status=(
                "missing" if tools.snapshot is None and tools.snapshot_status == "ready"
                else tools.snapshot_status
            ),
            snapshot_at=tools.snapshot_at,
            definitions=tools.snapshot.definitions if tools.snapshot is not None else [],
        ),
    )


@router.post("/mcp", response_model=McpServerResponse, status_code=status.HTTP_201_CREATED)
async def create_mcp_server(
    body: McpServerCreate,
    request: Request,
    store: McpServerStoreBase = Depends(get_mcp_store),
) -> McpServerResponse:
    if body.name == _RESERVED_MCP_NAME:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="该名称保留给 MCP 应用目录接口。")
    existing = await store.get_by_name(body.name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"MCP server already exists: {body.name}",
        )
    inspection = None
    if body.transport == "url":
        candidate = SimpleNamespace(
            name=body.name,
            transport=body.transport,
            config=body.config,
            description=body.description,
            inspection=None,
        )
        if not _bound_for_save(candidate):
            result = await _inspect_row(candidate, request)
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
    bound = config_changed and _bound_for_save(candidate)
    if bound:
        config_changed = candidate.transport != row.transport or candidate.config != row.config
    if config_changed:
        candidate.inspection = mark_mcp_inspection_stale(row).to_storage_value()
        if not bound and _should_reinspect(row, body):
            result = await _inspect_row(candidate, request)
            candidate.inspection = result.inspection.to_storage_value()
    try:
        if config_changed:
            updated = await store.update(name, body, inspection=candidate.inspection, expected=row)
        else:
            updated = await store.update(name, body, expected=row)
    except ReferenceConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"MCP server not found: {name}")
    return McpServerResponse.from_row(updated)


@router.post("/mcp/{name}/rename", response_model=McpServerRenameResponse)
async def rename_mcp_server(
    name: str,
    body: McpServerRenameRequest,
    store: McpServerStoreBase = Depends(get_mcp_store),
) -> McpServerRenameResponse:
    if body.new_name == _RESERVED_MCP_NAME:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="该名称保留给 MCP 应用目录接口。")
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
    try:
        deleted = await store.delete(name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    return await _inspect_and_store(store, row, request)
