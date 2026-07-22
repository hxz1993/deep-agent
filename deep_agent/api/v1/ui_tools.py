from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from deep_agent.capabilities.ui.builtin import builtin_ui_records
from deep_agent.capabilities.ui.registry import merge_definitions
from deep_agent.capabilities.ui.types import UIToolRecord
from deep_agent.db.deps import get_ui_tool_store
from deep_agent.db.stores.ui_tool import UiToolStoreBase
from deep_agent.schemas.ui_tools import UiToolCreate, UiToolListResponse, UiToolResponse, UiToolUpdate

router = APIRouter()
_BUILTIN_RECORDS = {record.name: record for record in builtin_ui_records()}
_BUILTIN_NAMES = set(_BUILTIN_RECORDS)


def _include_builtins(*, source: str | None, mcp_server: str | None, enabled: bool | None) -> bool:
    return source in {None, "builtin"} and mcp_server is None and enabled is not False


@router.get("/ui-tools", response_model=UiToolListResponse)
async def list_ui_tools(
    store: UiToolStoreBase = Depends(get_ui_tool_store),
    mcp_server: str | None = Query(default=None),
    source: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
) -> UiToolListResponse:
    rows = await store.list_all(
        source=source,
        mcp_server_name=mcp_server,
        enabled=enabled,
    )

    rows = [row for row in rows if row.source != "builtin"]
    builtins = builtin_ui_records() if _include_builtins(source=source, mcp_server=mcp_server, enabled=enabled) else []
    db_records = [UIToolRecord.from_row(row) for row in rows]
    merged, _ = merge_definitions(builtins, db_records)
    winners = {record.name: record for record in merged}

    items: list[UiToolResponse] = []
    for record in builtins:
        winner = winners.get(record.name)
        shadowed_by = winner.source if winner is not None and winner.source != record.source else None
        items.append(UiToolResponse.from_record(record, shadowed_by=shadowed_by))
    for row in rows:
        winner = winners.get(row.name)
        shadowed_by = None
        if winner is not None and winner.source != row.source:
            shadowed_by = winner.source
        items.append(UiToolResponse.from_row(row, shadowed_by=shadowed_by))

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
) -> UiToolResponse:
    if body.name in _BUILTIN_NAMES and not body.override:
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
    row = await store.create_manual(body)
    return UiToolResponse.from_row(row)


@router.patch("/ui-tools/{name}", response_model=UiToolResponse)
async def update_ui_tool(
    name: str,
    body: UiToolUpdate,
    store: UiToolStoreBase = Depends(get_ui_tool_store),
    force: bool = Query(default=False),
) -> UiToolResponse:
    row = await store.get_by_name(name)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"UI tool not found: {name}")
    if row.source == "builtin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Builtin UI tools cannot be modified")
    if row.source == "mcp" and not force:
        allowed = {"enabled", "override"}
        updates = body.model_dump(exclude_unset=True)
        if set(updates) - allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MCP-discovered tools only allow enabled and override unless force=true",
            )
    updated = await store.update(name, body)
    assert updated is not None
    return UiToolResponse.from_row(updated)


@router.delete("/ui-tools/{name}", status_code=status.HTTP_200_OK)
async def delete_ui_tool(
    name: str,
    store: UiToolStoreBase = Depends(get_ui_tool_store),
) -> None:
    row = await store.get_by_name(name)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"UI tool not found: {name}")
    if row.source == "builtin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Builtin UI tools cannot be deleted")
    await store.delete(name)
