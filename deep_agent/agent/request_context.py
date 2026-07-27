from __future__ import annotations

from dataclasses import dataclass
import re


DEFAULT_LANGUAGE = "zh-cn"
_APP_CODE_PATTERN = re.compile(r"^.+\d{3}$")


@dataclass(frozen=True)
class ResolvedRequestContext:
    app: str | None
    space: str | None
    language: str


def _normalize(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


def derive_space_from_app(app: str) -> str:
    normalized = _normalize(app)
    if normalized is None or _APP_CODE_PATTERN.fullmatch(normalized) is None:
        raise ValueError(
            "app 应由 Space 编码和三位数字组成，例如 mdxath003"
        )
    return normalized[:-3]


def resolve_request_context(
    *,
    app: str | None,
    space: str | None,
    language: str | None,
) -> ResolvedRequestContext:
    normalized_app = _normalize(app)
    normalized_space = _normalize(space)
    normalized_language = _normalize(language) or DEFAULT_LANGUAGE

    if normalized_app is not None:
        derived_space = derive_space_from_app(normalized_app)
        if normalized_space is not None and normalized_space != derived_space:
            raise ValueError(
                "space 与 app 编码不匹配；"
                f"app={normalized_app} 对应的 space 应为 {derived_space}"
            )
        normalized_space = derived_space

    return ResolvedRequestContext(
        app=normalized_app,
        space=normalized_space,
        language=normalized_language,
    )
