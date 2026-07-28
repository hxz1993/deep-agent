from __future__ import annotations

import re

RESERVED_TOOL_NAMES = frozenset({"builtin__confirm", "builtin__choice"})
TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,120}$")
LOCAL_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

MAX_SCHEMA_BYTES = 16 * 1024
MAX_DESCRIPTION_LEN = 2048

SOURCE_PRIORITY = {"builtin": 0, "manual": 1}
