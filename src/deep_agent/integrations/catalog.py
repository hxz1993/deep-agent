from dataclasses import dataclass
from enum import StrEnum


class IntegrationCode(StrEnum):
    DEEPTABLE_ASSISTANT = "deeptable-assistant"
    DEEPCUBE_ASSISTANT = "deepcube-assistant"


@dataclass(frozen=True)
class IntegrationDefinition:
    code: IntegrationCode
    name: str
    group_code: str
    group_name: str
    group_description: str
    group_icon: str


INTEGRATION_DEFINITIONS = (
    IntegrationDefinition(IntegrationCode.DEEPTABLE_ASSISTANT, "电子表格助手", "deeptable", "电子表格", "电子表格组件的专属能力配置。", "table-2"),
    IntegrationDefinition(IntegrationCode.DEEPCUBE_ASSISTANT, "DeepCube 助手", "python-server", "Python Server", "Python Server 组件的专属能力配置。", "square-code"),
)


def get_integration_definition(code: str) -> IntegrationDefinition | None:
    return next((item for item in INTEGRATION_DEFINITIONS if item.code == code), None)
