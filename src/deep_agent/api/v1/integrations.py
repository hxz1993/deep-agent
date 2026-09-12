from typing import Annotated

from fastapi import APIRouter, HTTPException, Path

from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.api.v1.integration_routes import IntegrationRoute
from deep_agent.core.context import AppContextDep
from deep_agent.integrations.catalog import INTEGRATION_DEFINITIONS, IntegrationCode, get_integration_definition
from deep_agent.schemas.integration import IntegrationCatalog, IntegrationSettingsWrite, IntegrationGroup, IntegrationItem

router = APIRouter(dependencies=CONSOLE_ADMIN_DEPENDENCIES, route_class=IntegrationRoute)
tag = "integrations"
IntegrationCodePath = Annotated[str, Path(description="集成编码", json_schema_extra={"enum": [item.value for item in IntegrationCode]})]


def require_definition(code: str):
    definition = get_integration_definition(code)
    if definition is None:
        raise HTTPException(404, detail={"code": "integration_not_found", "message": "未知的集成编码", "integration_code": code})
    return definition


def get_store(ctx):
    store = ctx.stores.integration_settings
    if store is None:
        raise HTTPException(503, detail="Integration configuration store is unavailable")
    return store


def integration_item(definition, row=None) -> IntegrationItem:
    return IntegrationItem(code=definition.code, name=definition.name, settings=row.settings if row else {}, created_at=row.created_at if row else None, updated_at=row.updated_at if row else None)


@router.get("/integrations", response_model=IntegrationCatalog)
async def list_integrations(ctx: AppContextDep) -> IntegrationCatalog:
    """固定集成目录叠加已保存配置；读取不创建数据库记录。"""
    rows = {row.code: row for row in await get_store(ctx).list_all()}
    groups = {}
    for definition in INTEGRATION_DEFINITIONS:
        if definition.group_code not in groups:
            groups[definition.group_code] = IntegrationGroup(code=definition.group_code, name=definition.group_name, description=definition.group_description, icon=definition.group_icon, integrations=[])
        groups[definition.group_code].integrations.append(integration_item(definition, rows.get(definition.code)))
    return IntegrationCatalog(groups=list(groups.values()))


@router.put("/integrations/{code}", response_model=IntegrationItem)
async def save_integration_configuration(code: IntegrationCodePath, body: IntegrationSettingsWrite, ctx: AppContextDep) -> IntegrationItem:
    """替换此集成的完整 JSON 配置；空对象恢复未配置状态。"""
    definition = require_definition(code)
    return integration_item(definition, await get_store(ctx).save(code, body.settings))


@router.delete("/integrations/{code}", response_model=IntegrationItem)
async def clear_integration_configuration(code: IntegrationCodePath, ctx: AppContextDep) -> IntegrationItem:
    """清除已保存配置，保留固定目录定义；重复清除成功。"""
    definition = require_definition(code)
    await get_store(ctx).delete(code)
    return integration_item(definition)
