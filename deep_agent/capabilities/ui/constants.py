from __future__ import annotations

import re

UI_TOOLS_LIST_TOOL = "ui_tools_list"
UI_TOOLS_MANIFEST_URI = "ui://tools/manifest"
RESERVED_TOOL_NAMES = frozenset({UI_TOOLS_LIST_TOOL, "confirm_action", "ask_user_choice"})
TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,120}$")
LOCAL_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

MAX_UI_TOOLS = 256
MAX_TOOLS_PER_MCP = 32
MAX_SCHEMA_BYTES = 16 * 1024
MAX_DESCRIPTION_LEN = 2048
DISCOVERY_TIMEOUT_SEC = 10.0

SOURCE_PRIORITY = {"manual": 0, "mcp": 1, "builtin": 2}


def canonical_mcp_tool_name(mcp_server_name: str, local_name: str) -> str:
    return f"{mcp_server_name}__{local_name}"
