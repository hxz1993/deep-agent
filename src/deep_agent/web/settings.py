from __future__ import annotations

from dataclasses import dataclass, replace
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
WebSearchCredentialSource = Literal["api_key", "llm_provider"]

_PROVIDERS = frozenset({"deepseek", "kimi", "tavily", "exa", "you", "duckduckgo"})
_MODEL_DEFAULTS = {
    "deepseek": "deepseek-v4-pro",
    "kimi": "kimi-k3",
}
_KEYLESS_PROVIDERS = frozenset({"duckduckgo"})
_REUSABLE_LLM_PROVIDER_TYPES = {
    "deepseek": "deepseek",
    "kimi": "moonshotai",
}


class WebSearchConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class WebSearchRuntimeConfig:
    mode: Literal["native", "provider"]
    provider: WebSearchProvider | None = None
    api_key: str | None = None
    model: str | None = None
    credential_source: WebSearchCredentialSource | None = None
    llm_provider_id: str | None = None
    llm_provider_name: str | None = None
    # Runtime-only: never persist a second copy of the provider endpoint.
    base_url: str | None = None

    def to_stored_value(self) -> dict[str, Any]:
        if self.mode == "native":
            return {"mode": "native"}
        value: dict[str, Any] = {
            "mode": "provider",
            "provider": self.provider,
        }
        if self.api_key and self.credential_source != "llm_provider":
            value["api_key"] = self.api_key
        if self.model:
            value["model"] = self.model
        if self.credential_source:
            value["credential_source"] = self.credential_source
        if self.credential_source == "llm_provider" and self.llm_provider_id:
            value["llm_provider_id"] = self.llm_provider_id
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

    credential_source: WebSearchCredentialSource | None
    if provider in _KEYLESS_PROVIDERS:
        credential_source = None
    elif provider in _REUSABLE_LLM_PROVIDER_TYPES:
        raw_source = _normalized_optional(raw.get("credential_source"))
        if raw_source is None:
            raw_source = "llm_provider" if raw.get("llm_provider_id") else "api_key"
        if raw_source not in {"api_key", "llm_provider"}:
            raise WebSearchConfigurationError(
                "credential_source must be api_key or llm_provider"
            )
        credential_source = raw_source  # type: ignore[assignment]
    else:
        credential_source = "api_key"

    existing_key = (
        existing.api_key
        if existing is not None
        and existing.mode == "provider"
        and existing.provider == provider
        and existing.credential_source == "api_key"
        else None
    )
    existing_llm_provider_id = (
        existing.llm_provider_id
        if existing is not None
        and existing.mode == "provider"
        and existing.provider == provider
        and existing.credential_source == "llm_provider"
        else None
    )
    llm_provider_id = None
    api_key = None
    if provider in _KEYLESS_PROVIDERS:
        pass
    elif credential_source == "llm_provider":
        llm_provider_id = (
            _normalized_optional(raw.get("llm_provider_id"))
            or existing_llm_provider_id
        )
        if require_credentials and not llm_provider_id:
            raise WebSearchConfigurationError(
                f"A configured LLM provider is required for {provider}"
            )
    else:
        api_key = _normalized_optional(raw.get("api_key")) or existing_key
        if require_credentials and not api_key:
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
        credential_source=credential_source,
        llm_provider_id=llm_provider_id,
    )


async def resolve_web_search_credentials(
    config: WebSearchRuntimeConfig,
    llm_configuration: Any,
) -> WebSearchRuntimeConfig:
    if config.credential_source != "llm_provider":
        return config
    if not config.provider or not config.llm_provider_id:
        raise WebSearchConfigurationError("Configured LLM provider reference is missing")
    row = await llm_configuration.get_provider(config.llm_provider_id)
    if row is None:
        raise WebSearchConfigurationError(
            f"Configured LLM provider no longer exists: {config.llm_provider_id}"
        )
    expected_type = _REUSABLE_LLM_PROVIDER_TYPES.get(config.provider)
    if str(row.provider_type) != expected_type:
        raise WebSearchConfigurationError(
            f"Selected LLM provider must be {expected_type} for {config.provider} search"
        )
    api_key = _normalized_optional(row.api_key)
    if not api_key:
        raise WebSearchConfigurationError(
            f"Selected LLM provider has no API key: {row.name}"
        )
    from deep_agent.models.provider_registry import ProviderRuntimeConfig, effective_base_url

    return replace(
        config,
        api_key=api_key,
        llm_provider_name=str(row.name),
        base_url=effective_base_url(ProviderRuntimeConfig.from_row(row)),
    )


async def load_web_search_config(
    store: Any,
    llm_configuration: Any,
) -> WebSearchRuntimeConfig:
    row = await store.get(WEB_SEARCH_SETTING_KEY)
    if row is None:
        return WebSearchRuntimeConfig(mode="native")
    if int(row.version) != WEB_SEARCH_SCHEMA_VERSION:
        raise WebSearchConfigurationError(
            f"Unsupported Web Search settings version: {row.version}"
        )
    config = resolve_web_search_config(row.value)
    return await resolve_web_search_credentials(config, llm_configuration)
