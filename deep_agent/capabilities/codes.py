from __future__ import annotations

from collections.abc import Iterable


PTC_PACKAGE_CODE = "builtin:ptc"
LEGACY_CODE_MODE_PACKAGE_CODE = "builtin:code-mode"

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
        code = canonical_tool_package_code(raw_code)
        if code in seen:
            continue
        seen.add(code)
        result.append(code)
    return result


def is_ptc_selected(codes: Iterable[str] | None) -> bool:
    return PTC_PACKAGE_CODE in canonical_tool_package_codes(codes)
