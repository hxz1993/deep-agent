from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from deep_agent.capabilities.ui.builtin import builtin_ui_records
from deep_agent.db.deps import get_agent_store, get_ui_tool_package_store
from deep_agent.db.stores.agent import AgentStoreBase
from deep_agent.db.stores.ui_tool_package import UiToolPackageStoreBase
from deep_agent.schemas.ui_tools import (
    UiToolPackageCreate,
    UiToolPackageListResponse,
    UiToolPackageResponse,
    UiToolPackageUpdate,
)

router = APIRouter()
BUILTIN_UI_PACKAGE_CODE = "ui:builtin"


def _builtin_response() -> UiToolPackageResponse:
    names = [record.name for record in builtin_ui_records()]
    return UiToolPackageResponse(
        code=BUILTIN_UI_PACKAGE_CODE,
        label="通用交互",
        description="代码内置的确认与候选选择交互。",
        source="builtin",
        enabled=True,
        tool_count=len(names),
        tools=names,
    )


@router.get("/ui-tool-packages", response_model=UiToolPackageListResponse)
async def list_ui_tool_packages(
    store: UiToolPackageStoreBase = Depends(get_ui_tool_package_store),
) -> UiToolPackageListResponse:
    rows = await store.list_all()
    items = [_builtin_response()]
    for row in rows:
        names = await store.list_tool_names(row.code)
        items.append(UiToolPackageResponse.from_row(row, tools=names))
    return UiToolPackageListResponse(items=items, total=len(items))


@router.get("/ui-tool-packages/{code:path}", response_model=UiToolPackageResponse)
async def get_ui_tool_package(
    code: str,
    store: UiToolPackageStoreBase = Depends(get_ui_tool_package_store),
) -> UiToolPackageResponse:
    if code == BUILTIN_UI_PACKAGE_CODE:
        return _builtin_response()
    row = await store.get_by_code(code)
    if row is None:
        raise HTTPException(status_code=404, detail=f"UI tool package not found: {code}")
    return UiToolPackageResponse.from_row(row, tools=await store.list_tool_names(code))


@router.post(
    "/ui-tool-packages",
    response_model=UiToolPackageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_ui_tool_package(
    body: UiToolPackageCreate,
    store: UiToolPackageStoreBase = Depends(get_ui_tool_package_store),
) -> UiToolPackageResponse:
    if body.code == BUILTIN_UI_PACKAGE_CODE:
        raise HTTPException(status_code=409, detail="Builtin UI tool package code is reserved")
    if await store.get_by_code(body.code) is not None:
        raise HTTPException(status_code=409, detail=f"UI tool package already exists: {body.code}")
    try:
        row = await store.create_manual(body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return UiToolPackageResponse.from_row(row)


@router.patch("/ui-tool-packages/{code:path}", response_model=UiToolPackageResponse)
async def update_ui_tool_package(
    code: str,
    body: UiToolPackageUpdate,
    store: UiToolPackageStoreBase = Depends(get_ui_tool_package_store),
) -> UiToolPackageResponse:
    if code == BUILTIN_UI_PACKAGE_CODE:
        raise HTTPException(status_code=403, detail="Builtin UI tool package cannot be modified")
    row = await store.get_by_code(code)
    if row is None:
        raise HTTPException(status_code=404, detail=f"UI tool package not found: {code}")
    updated = await store.update(code, body)
    assert updated is not None
    return UiToolPackageResponse.from_row(
        updated, tools=await store.list_tool_names(updated.code)
    )


@router.delete("/ui-tool-packages/{code:path}", status_code=status.HTTP_200_OK)
async def delete_ui_tool_package(
    code: str,
    store: UiToolPackageStoreBase = Depends(get_ui_tool_package_store),
    agent_store: AgentStoreBase = Depends(get_agent_store),
) -> None:
    if code == BUILTIN_UI_PACKAGE_CODE:
        raise HTTPException(status_code=403, detail="Builtin UI tool package cannot be deleted")
    row = await store.get_by_code(code)
    if row is None:
        raise HTTPException(status_code=404, detail=f"UI tool package not found: {code}")
    tool_names = await store.list_tool_names(code)
    referenced_by = await agent_store.list_referencing_capability(code)
    if tool_names or referenced_by:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "UI 工具包包含工具或正在被 Agent 引用，不能删除",
                "resource": code,
                "tools": tool_names,
                "referenced_by": [agent.name for agent in referenced_by],
            },
        )
    await store.delete(code)
