from __future__ import annotations

from pydantic_ai import ExternalToolset

from deep_agent.capabilities.ui.types import UIToolRecord

_CONFIRM_ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "可选的确认界面标题。",
        },
        "message": {
            "type": "string",
            "description": "需要用户确认或取消的具体内容。",
        },
        "confirm_label": {
            "type": "string",
            "description": "确认按钮文案。",
            "default": "确认",
        },
        "cancel_label": {
            "type": "string",
            "description": "取消按钮文案。",
            "default": "取消",
        },
    },
    "required": ["message"],
}

_CHOICE_OPTION_SCHEMA = {
    "oneOf": [
        {"type": "string"},
        {
            "type": "object",
            "properties": {
                "value": {
                    "type": "string",
                    "description": "提交给模型的稳定选项值。",
                },
                "label": {
                    "type": "string",
                    "description": "展示给用户的选项名称。",
                },
                "description": {
                    "type": "string",
                    "description": "可选的补充说明。",
                },
            },
            "required": ["value"],
            "additionalProperties": False,
        },
    ],
}

_ASK_USER_CHOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "可选的选择界面标题。",
        },
        "prompt": {
            "type": "string",
            "description": "需要用户选择或补充回答的问题。",
        },
        "options": {
            "type": "array",
            "items": _CHOICE_OPTION_SCHEMA,
            "minItems": 1,
            "description": "候选项；可使用字符串或包含 value、label、description 的对象。",
        },
        "allow_multiple": {
            "type": "boolean",
            "description": "是否允许同时选择多个候选项。",
            "default": False,
        },
        "custom_label": {
            "type": "string",
            "description": "自由输入选项的展示文案。",
            "default": "其他（自行输入）",
        },
        "custom_placeholder": {
            "type": "string",
            "description": "自由输入框的占位提示。",
            "default": "请输入其他内容",
        },
        "submit_label": {
            "type": "string",
            "description": "提交按钮文案。",
            "default": "提交",
        },
    },
    "required": ["prompt", "options"],
}

_CONFIRM_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "confirmed": {
            "type": "boolean",
            "description": "用户是否确认继续。",
        },
    },
    "required": ["confirmed"],
    "additionalProperties": False,
}

_CHOICE_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "selected": {
            "type": "array",
            "items": {"type": "string"},
            "description": "用户选择的候选项 value；未选择候选项时为空数组。",
        },
        "custom_input": {
            "type": ["string", "null"],
            "description": "用户填写的候选外答案；未填写时为 null。",
        },
    },
    "required": ["selected", "custom_input"],
    "additionalProperties": False,
}


def builtin_ui_records() -> list[UIToolRecord]:
    return [
        UIToolRecord(
            name="builtin__confirm",
            package_code="ui:builtin",
            description=(
                "向用户展示通用确认界面并暂停当前运行，等待用户选择继续或取消。"
                "适用于确认一个明确步骤，不代表后端强制审批或权限校验。"
            ),
            parameters_json_schema=_CONFIRM_ACTION_SCHEMA,
            widget="confirm",
            result_schema=_CONFIRM_RESULT_SCHEMA,
            source="builtin",
        ),
        UIToolRecord(
            name="builtin__choice",
            package_code="ui:builtin",
            description=(
                "向用户展示通用候选列表并暂停当前运行，支持单选、多选以及候选外自由输入。"
            ),
            parameters_json_schema=_ASK_USER_CHOICE_SCHEMA,
            widget="choice",
            result_schema=_CHOICE_RESULT_SCHEMA,
            source="builtin",
        ),
    ]


def build_ui_toolset() -> ExternalToolset:
    """Build the code-owned builtin UI package's external toolset."""
    from deep_agent.capabilities.ui.registry import build_external_toolset

    return build_external_toolset(
        builtin_ui_records(), toolset_id="ui-package:ui:builtin"
    )
