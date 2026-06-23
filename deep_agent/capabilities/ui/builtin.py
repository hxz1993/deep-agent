from __future__ import annotations

from pydantic_ai import ExternalToolset

from deep_agent.capabilities.registry import Capability, ToolInfo
from deep_agent.capabilities.ui.types import UIToolRecord

_CONFIRM_ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {
            "type": "string",
            "description": "Confirmation prompt shown to the user.",
        },
        "confirm_label": {
            "type": "string",
            "description": "Label for the confirm button.",
            "default": "确认",
        },
        "cancel_label": {
            "type": "string",
            "description": "Label for the cancel button.",
            "default": "取消",
        },
    },
    "required": ["message"],
}

_ASK_USER_CHOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "Question or instruction shown above the choices.",
        },
        "options": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "Selectable options presented to the user.",
        },
        "allow_multiple": {
            "type": "boolean",
            "description": "Whether the user may select more than one option.",
            "default": False,
        },
    },
    "required": ["prompt", "options"],
}


def builtin_ui_records() -> list[UIToolRecord]:
    return [
        UIToolRecord(
            name="confirm_action",
            description=(
                "Ask the user to confirm or cancel an action. "
                "Execution happens in the UI; wait for the user's response."
            ),
            parameters_json_schema=_CONFIRM_ACTION_SCHEMA,
            widget="confirm",
            source="builtin",
        ),
        UIToolRecord(
            name="ask_user_choice",
            description=(
                "Present options and wait for the user to pick one or more. "
                "Execution happens in the UI."
            ),
            parameters_json_schema=_ASK_USER_CHOICE_SCHEMA,
            widget="choice",
            source="builtin",
        ),
    ]


def build_ui_toolset() -> ExternalToolset:
    """Frontend-executed tools; calls are deferred until the user responds."""
    from deep_agent.capabilities.ui.registry import build_external_toolset

    return build_external_toolset(builtin_ui_records(), toolset_id="ui")


def build_ui_capability() -> Capability:
    toolset = build_ui_toolset()
    return Capability(
        name="ui",
        kind="ui",
        toolset=toolset,
        description="Human-in-the-loop UI tools (confirm, choice).",
        tools=[
            ToolInfo("confirm_action", "Show a confirm/cancel dialog."),
            ToolInfo("ask_user_choice", "Show a single or multi-select choice list."),
        ],
        default_enabled=True,
    )
