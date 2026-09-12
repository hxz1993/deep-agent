"""One API description shared by OpenAPI and the integration Console dialog."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, JsonValue

from deep_agent.integrations.catalog import INTEGRATION_DEFINITIONS, get_integration_definition

CONFIGURATION_PATH = "/api/v1/integrations/{code}"
CONFIGURATION_DESCRIPTION = "读取此集成使用的 Agent，返回 agent_id，供组件发起对话。"

ERRORS = (
    {"status": 401, "code": "", "message": "需要已认证的平台用户"},
    {"status": 404, "code": "integration_not_found", "message": "未知的集成编码"},
    {"status": 409, "code": "integration_not_configured", "message": "该集成尚未配置"},
)


class IntegrationSettingsResponse(BaseModel):
    code: str = Field(description="集成编码")
    settings: dict[str, JsonValue] = Field(description="组件可读取的 JSON 配置；保留值的原始 JSON 类型")


class IntegrationApiDescription(BaseModel):
    title: str
    description: str
    method: str
    path: str
    parameters: list[dict[str, Any]]
    request_example: dict[str, Any]
    response_example: IntegrationSettingsResponse
    errors: list[dict[str, Any]]


def configuration_example(code: str) -> dict:
    """Illustrative only; never seed or derive a real Agent binding."""
    return {"code": code, "settings": {"agent_id": "00000000-0000-0000-0000-000000000001"}}


def api_description(code: str) -> IntegrationApiDescription:
    definition = get_integration_definition(code)
    if definition is None:
        raise ValueError("Unknown integration code")
    path = CONFIGURATION_PATH.format(code=code)
    return IntegrationApiDescription(
        title=f"{definition.name}接口",
        description=CONFIGURATION_DESCRIPTION,
        method="GET", path=path,
        parameters=[{"name": "code", "location": "path", "required": True,
                     "description": "集成编码", "example": code}],
        request_example={"method": "GET", "path": path},
        response_example=configuration_example(code), errors=list(ERRORS),
    )


def configuration_responses() -> dict:
    codes = [definition.code for definition in INTEGRATION_DEFINITIONS]
    responses = {200: {"description": "已保存的集成配置", "content": {"application/json": {
        "examples": {code: {"summary": code, "value": configuration_example(code)} for code in codes}
    }}}}
    for error in ERRORS:
        detail: Any = "Missing authenticated user header" if error["status"] == 401 else {
            "code": error["code"], "message": error["message"], "integration_code": codes[0],
        }
        responses[error["status"]] = {"description": error["message"], "content": {
            "application/json": {"example": {"detail": detail}},
        }}
    return responses
