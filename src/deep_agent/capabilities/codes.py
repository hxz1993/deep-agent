from __future__ import annotations

from collections.abc import Iterable


PTC_PACKAGE_CODE = "builtin:ptc"
LEGACY_CODE_MODE_PACKAGE_CODE = "builtin:code-mode"
RETIRED_GENERAL_PACKAGE_CODE = "builtin:general"
ENVIRONMENT_TOOL_NAMES = frozenset({"get_current_time"})


def environment_name_conflict(name: str) -> str:
    return f"编码 {name} 与默认运行时工具冲突，请更换自定义工具或输出编码。"


_TOOL_PACKAGE_CODE_ALIASES = {
    LEGACY_CODE_MODE_PACKAGE_CODE: PTC_PACKAGE_CODE,
}


def canonical_tool_package_code(code: str) -> str:
    """Map a persisted or imported package code to its public canonical value."""
    return _TOOL_PACKAGE_CODE_ALIASES.get(code, code)


def canonical_tool_package_codes(codes: Iterable[str] | None) -> list[str]:
    """Canonicalize aliases and stably collapse alias-induced duplicates."""
    result: list[str] = []
    seen: set[str] = set()
    for raw_code in codes or ():
        # This former selectable package is now an unconditional runtime tool.
        # Accept legacy inputs without retaining a selectable/portable reference.
        if raw_code == RETIRED_GENERAL_PACKAGE_CODE:
            continue
        code = canonical_tool_package_code(raw_code)
        if code in seen:
            continue
        seen.add(code)
        result.append(code)
    return result


def is_ptc_selected(codes: Iterable[str] | None) -> bool:
    return PTC_PACKAGE_CODE in canonical_tool_package_codes(codes)
