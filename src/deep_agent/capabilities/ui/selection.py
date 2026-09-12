"""Agent-specific UI tool scope; absence means the legacy whole-package scope."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping


def normalize_ui_tool_selection(value: dict[str, list[str]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for code, names in value.items():
        if not re.fullmatch(r"ui:[a-z][a-z0-9_-]{1,120}", code):
            raise ValueError(f"Invalid UI package code: {code}")
        if any(not re.fullmatch(r"[a-z][a-z0-9_]{1,120}", name) for name in names):
            raise ValueError(f"Invalid UI tool name in {code}")
        if len(names) != len(set(names)):
            raise ValueError(f"UI tool names must be unique: {code}")
        result[code] = list(names)
    return result


def validate_ui_tool_selection(
    selection: dict[str, list[str]],
    capabilities: Iterable[str],
    tool_packages: Mapping[str, str],
) -> None:
    from deep_agent.capabilities.ui.builtin import builtin_ui_records

    normalized = normalize_ui_tool_selection(selection)
    ownership = dict(tool_packages)
    ownership.update({record.name: record.package_code for record in builtin_ui_records()})
    associated = set(capabilities)
    for code, names in normalized.items():
        if code not in associated:
            raise ValueError(f"UI tool selection references an unassociated package: {code}")
        for name in names:
            if ownership.get(name) != code:
                raise ValueError(f"UI tool does not belong to {code}: {name}")
