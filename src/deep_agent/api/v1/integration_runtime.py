"""Client-facing integration configuration and the Console's API help."""
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path

from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.api.v1.integration_routes import IntegrationRoute
from deep_agent.api.v1.integrations import get_store, require_definition
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.core.context import AppContextDep
from deep_agent.integrations.catalog import INTEGRATION_DEFINITIONS
from deep_agent.schemas.integration_api import (
    CONFIGURATION_DESCRIPTION, IntegrationApiDescription, IntegrationSettingsResponse,
    api_description, configuration_responses,
)

router = APIRouter(prefix="/integrations", route_class=IntegrationRoute)
tag = "integrations"
IntegrationCodePath = Annotated[str, Path(
    description="集成编码", json_schema_extra={"enum": [item.code for item in INTEGRATION_DEFINITIONS]},
)]


@router.get(
    "/{code}", summary="读取集成配置", description=CONFIGURATION_DESCRIPTION,
    response_model=IntegrationSettingsResponse, responses=configuration_responses(),
)
async def read_integration_configuration(
    code: IntegrationCodePath, user: RequestUserDep, ctx: AppContextDep,
) -> IntegrationSettingsResponse:
    require_definition(code)
    row = await get_store(ctx).get_by_code(code)
    if row is None or not row.settings:
        raise HTTPException(status_code=409, detail={
            "code": "integration_not_configured", "message": "该集成尚未配置", "integration_code": code,
        })
    return IntegrationSettingsResponse(code=code, settings=row.settings)


@router.get(
    "/{code}/api", summary="查看集成接口说明", response_model=IntegrationApiDescription,
    dependencies=CONSOLE_ADMIN_DEPENDENCIES,
)
async def get_integration_api(code: IntegrationCodePath) -> IntegrationApiDescription:
    require_definition(code)
    return api_description(code)
