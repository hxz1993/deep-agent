from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping


WEB_SEARCH_SETTING_KEY = "web_search"
WEB_SEARCH_SCHEMA_VERSION = 1
WEB_PACKAGE_CODE = "builtin:web"
WebSearchProvider = Literal[
    "deepseek",
    "kimi",
    "tavily",
    "exa",
    "you",
    "duckduckgo",
]

_PROVIDERS = frozenset({"deepseek", "kimi", "tavily", "exa", "you", "duckduckgo"})
_MODEL_DEFAULTS = {
    "deepseek": "deepseek-v4-flash",
    "kimi": "kimi-k3",
}
_KEYLESS_PROVIDERS = frozenset({"duckduckgo"})


class WebSearchConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class WebSearchRuntimeConfig:
    mode: Literal["native", "provider"]
    provider: WebSearchProvider | None = None
    api_key: str | None = None
    model: str | None = None

    def to_stored_value(self) -> dict[str, Any]:
        if self.mode == "native":
            return {"mode": "native"}
        value: dict[str, Any] = {
            "mode": "provider",
            "provider": self.provider,
        }
        if self.api_key:
            value["api_key"] = self.api_key
        if self.model:
            value["model"] = self.model
        return value


def _normalized_optional(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def resolve_web_search_config(
    value: Mapping[str, Any] | None,
    *,
    existing: WebSearchRuntimeConfig | None = None,
    require_credentials: bool = True,
) -> WebSearchRuntimeConfig:
    raw = dict(value or {"mode": "native"})
    mode = _normalized_optional(raw.get("mode")) or "native"
    if mode == "native":
        return WebSearchRuntimeConfig(mode="native")
    if mode != "provider":
        raise WebSearchConfigurationError("mode must be native or provider")

    provider = _normalized_optional(raw.get("provider"))
    if provider not in _PROVIDERS:
        raise WebSearchConfigurationError("A supported Web Search Provider is required")

    existing_key = (
        existing.api_key
        if existing is not None
        and existing.mode == "provider"
        and existing.provider == provider
        else None
    )
    api_key = _normalized_optional(raw.get("api_key")) or existing_key
    if provider in _KEYLESS_PROVIDERS:
        api_key = None
    elif require_credentials and not api_key:
        raise WebSearchConfigurationError(f"API key is required for {provider}")

    model = _normalized_optional(raw.get("model"))
    if provider in _MODEL_DEFAULTS:
        model = model or _MODEL_DEFAULTS[provider]
    else:
        model = None

    return WebSearchRuntimeConfig(
        mode="provider",
        provider=provider,  # type: ignore[arg-type]
        api_key=api_key,
        model=model,
    )


async def load_web_search_config(store: Any) -> WebSearchRuntimeConfig:
    row = await store.get(WEB_SEARCH_SETTING_KEY)
    if row is None:
        return WebSearchRuntimeConfig(mode="native")
    if int(row.version) != WEB_SEARCH_SCHEMA_VERSION:
        raise WebSearchConfigurationError(
            f"Unsupported Web Search settings version: {row.version}"
        )
    return resolve_web_search_config(row.value)
