from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from deep_agent.capabilities.ui.builtin import builtin_ui_records
from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.capabilities.ui.types import UIToolRecord
from deep_agent.db.deps import get_agent_store, get_ui_tool_package_store, get_ui_tool_store
from deep_agent.db.stores.agent import AgentStoreBase
from deep_agent.db.stores.ui_tool import UiToolStoreBase
from deep_agent.db.stores.ui_tool_package import UiToolPackageStoreBase
from deep_agent.schemas.ui_tools import UiToolCreate, UiToolListResponse, UiToolResponse, UiToolUpdate

router = APIRouter(dependencies=CONSOLE_ADMIN_DEPENDENCIES)
_BUILTIN_RECORDS = {record.name: record for record in builtin_ui_records()}
_BUILTIN_NAMES = set(_BUILTIN_RECORDS)


@router.get("/ui-tools", response_model=UiToolListResponse)
async def list_ui_tools(
    store: UiToolStoreBase = Depends(get_ui_tool_store),
    source: str | None = Query(default=None),
) -> UiToolListResponse:
    rows = await store.list_all(source=source)

    rows = [
        row
        for row in rows
        if row.source != "builtin" and row.name not in _BUILTIN_NAMES
    ]
    builtins = builtin_ui_records() if source in {None, "builtin"} else []

    items: list[UiToolResponse] = []
    for record in builtins:
        items.append(UiToolResponse.from_record(record))
    for row in rows:
        items.append(UiToolResponse.from_row(row))

    items.sort(key=lambda item: (item.name, item.source))
    return UiToolListResponse(items=items, total=len(items))


@router.get("/ui-tools/{name}", response_model=UiToolResponse)
async def get_ui_tool(
    name: str,
    store: UiToolStoreBase = Depends(get_ui_tool_store),
) -> UiToolResponse:
    builtin = _BUILTIN_RECORDS.get(name)
    if builtin is not None:
        return UiToolResponse.from_record(builtin)
    row = await store.get_by_name(name)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"UI tool not found: {name}")
    return UiToolResponse.from_row(row)


@router.post("/ui-tools", response_model=UiToolResponse, status_code=status.HTTP_201_CREATED)
async def create_ui_tool(
    body: UiToolCreate,
    store: UiToolStoreBase = Depends(get_ui_tool_store),
    package_store: UiToolPackageStoreBase = Depends(get_ui_tool_package_store),
) -> UiToolResponse:
    if body.name in _BUILTIN_NAMES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Builtin tool name is reserved: {body.name}",
        )
    existing = await store.get_by_name(body.name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"UI tool already exists: {body.name}",
        )
    package = await package_store.get_by_code(body.package_code)
    if package is None or package.source != "manual":
        raise HTTPException(status_code=422, detail="Manual UI tool package does not exist")
    try:
        row = await store.create_manual(body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return UiToolResponse.from_row(row)


@router.patch("/ui-tools/{name}", response_model=UiToolResponse)
async def update_ui_tool(
    name: str,
    body: UiToolUpdate,
    store: UiToolStoreBase = Depends(get_ui_tool_store),
    package_store: UiToolPackageStoreBase = Depends(get_ui_tool_package_store),
) -> UiToolResponse:
    if name in _BUILTIN_NAMES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Builtin UI tools cannot be modified")
    row = await store.get_by_name(name)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"UI tool not found: {name}")
    if row.source == "builtin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Builtin UI tools cannot be modified")
    if body.package_code is not None:
        package = await package_store.get_by_code(body.package_code)
        if package is None or package.source != "manual":
            raise HTTPException(status_code=422, detail="Manual UI tool package does not exist")
    updated = await store.update(name, body)
    assert updated is not None
    return UiToolResponse.from_row(updated)


@router.delete("/ui-tools/{name}", status_code=status.HTTP_200_OK)
async def delete_ui_tool(
    name: str,
    store: UiToolStoreBase = Depends(get_ui_tool_store),
    agent_store: AgentStoreBase = Depends(get_agent_store),
) -> None:
    if name in _BUILTIN_NAMES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Builtin UI tools cannot be deleted")
    row = await store.get_by_name(name)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"UI tool not found: {name}")
    if row.source == "builtin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Builtin UI tools cannot be deleted")
    referenced_by = await agent_store.list_referencing_capability(row.package_code)
    if referenced_by:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "UI 工具正在被 Agent 引用，不能删除",
                "resource": name,
                "referenced_by": [agent.name for agent in referenced_by],
            },
        )
    await store.delete(name)
