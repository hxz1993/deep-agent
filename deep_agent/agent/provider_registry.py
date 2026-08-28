"""Provider type registry and Pydantic AI runtime model assembly.

Database provider rows select one of the finite provider types below.  The
registry is deliberately ordered for Console presentation and is also the
single source of truth for runtime construction; model names and URLs are not
used to guess a provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx2
from pydantic_ai.models import DEFAULT_HTTP_TIMEOUT, Model, get_user_agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.alibaba import AlibabaProvider
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.moonshotai import MoonshotAIProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.providers.zai import ZaiProvider

from deep_agent.agent.models import OpenAICompatibleVideoChatModel
from deep_agent.core.exceptions import DeepAgentError
from deep_agent.options import OPTION


@dataclass(frozen=True)
class ProviderTypeDefinition:
    code: str
    label: str
    order: int
    default_base_url: str | None
    supports_default_base_url: bool
    supports_custom_base_url: bool
    model_protocol: str


PROVIDER_TYPES: tuple[ProviderTypeDefinition, ...] = (
    ProviderTypeDefinition(
        "deepseek", "DeepSeek", 1, "https://api.deepseek.com", True, True, "openai_chat"
    ),
    ProviderTypeDefinition(
        "moonshotai",
        "MoonshotAI (Kimi)",
        2,
        "https://api.moonshot.ai/v1/",
        True,
        True,
        "openai_chat",
    ),
    ProviderTypeDefinition(
        "alibaba",
        "Alibaba (Qwen / DashScope)",
        3,
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/",
        True,
        True,
        "openai_chat",
    ),
    ProviderTypeDefinition(
        "zai",
        "Z.AI (GLM)",
        4,
        "https://api.z.ai/api/paas/v4/",
        True,
        True,
        "openai_chat",
    ),
    ProviderTypeDefinition(
        "openrouter",
        "OpenRouter",
        5,
        "https://openrouter.ai/api/v1/",
        True,
        True,
        "openai_chat",
    ),
    ProviderTypeDefinition(
        "openai", "OpenAI", 6, "https://api.openai.com/v1/", True, True, "openai_chat"
    ),
    ProviderTypeDefinition(
        "anthropic", "Anthropic", 7, "https://api.anthropic.com", True, True, "anthropic"
    ),
    ProviderTypeDefinition(
        "google",
        "Google Gemini",
        8,
        "https://generativelanguage.googleapis.com/",
        True,
        True,
        "google",
    ),
    ProviderTypeDefinition(
        "openai_compatible",
        "OpenAI Compatible",
        9,
        None,
        False,
        True,
        "openai_chat",
    ),
)
_PROVIDER_TYPES_BY_CODE = {item.code: item for item in PROVIDER_TYPES}


def provider_type_definition(code: str) -> ProviderTypeDefinition:
    """Return a provider definition without applying aliases or inference."""
    try:
        return _PROVIDER_TYPES_BY_CODE[code]
    except KeyError as exc:
        raise UnknownProviderTypeError(code) from exc


class ProviderRuntimeError(DeepAgentError):
    """A persisted provider cannot be used to start a model request."""


class UnknownProviderTypeError(ProviderRuntimeError):
    def __init__(self, provider_type: object) -> None:
        super().__init__(f"Unsupported LLM provider type: {provider_type}")
        self.provider_type = provider_type


class ProviderConfigurationError(ProviderRuntimeError):
    """A provider has incomplete or invalid runtime configuration."""


@dataclass(frozen=True)
class ProviderRuntimeConfig:
    id: str | None
    name: str
    provider_type: str
    use_default_base_url: bool
    base_url: str
    api_key: str

    @classmethod
    def from_row(cls, row: Any) -> "ProviderRuntimeConfig":
        return cls(
            id=str(row.id) if getattr(row, "id", None) is not None else None,
            name=str(getattr(row, "name", "") or "").strip(),
            provider_type=str(getattr(row, "provider_type", "") or "").strip(),
            use_default_base_url=bool(getattr(row, "use_default_base_url", False)),
            base_url=str(getattr(row, "base_url", "") or "").strip(),
            api_key=str(getattr(row, "api_key", "") or "").strip(),
        )


def _validate_http_url(value: str, *, field: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    try:
        parsed.port
    except ValueError as exc:
        raise ProviderConfigurationError(
            f"{field} must be a valid HTTP(S) URL"
        ) from exc
    if (
        any(character.isspace() for character in normalized)
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderConfigurationError(f"{field} must be a valid HTTP(S) URL")
    return normalized


def validate_provider_config(config: ProviderRuntimeConfig) -> ProviderTypeDefinition:
    definition = provider_type_definition(config.provider_type)
    if not config.api_key:
        raise ProviderConfigurationError(
            f"API key is required for LLM provider: {config.name or config.provider_type}"
        )
    if config.use_default_base_url:
        if not definition.supports_default_base_url or not definition.default_base_url:
            raise ProviderConfigurationError(
                f"Provider type {config.provider_type} has no default base URL"
            )
        _validate_http_url(definition.default_base_url, field="default_base_url")
    else:
        if not definition.supports_custom_base_url:
            raise ProviderConfigurationError(
                f"Provider type {config.provider_type} does not support a custom base URL"
            )
        if not config.base_url:
            raise ProviderConfigurationError(
                f"Custom base URL is required for LLM provider: {config.name or config.provider_type}"
            )
        _validate_http_url(config.base_url, field="base_url")
    return definition


def model_unavailable_reason(llm_model: Any, provider: Any) -> str | None:
    """Return a secret-free reason when a persisted model cannot be selected."""
    if getattr(llm_model, "enabled", True) is False:
        return "model_disabled"
    if not str(getattr(provider, "api_key", "") or "").strip():
        return "provider_unconfigured"
    try:
        validate_provider_config(ProviderRuntimeConfig.from_row(provider))
    except ProviderRuntimeError:
        return "provider_invalid"
    return None


def effective_base_url(config: ProviderRuntimeConfig) -> str:
    definition = validate_provider_config(config)
    value = definition.default_base_url if config.use_default_base_url else config.base_url
    assert value is not None
    return value.rstrip("/")


async def load_provider_for_model(
    llm_model: Any,
    llm_configuration_store: Any,
) -> Any:
    """Load and validate the explicit provider linked to a persisted model."""
    model_id = getattr(llm_model, "id", None) or "unknown"
    if getattr(llm_model, "enabled", True) is False:
        raise ProviderConfigurationError(f"LLM model is disabled: {model_id}")
    provider_id = getattr(llm_model, "provider_id", None)
    if not provider_id:
        raise ProviderConfigurationError(
            f"LLM provider is required for model {model_id}"
        )
    if llm_configuration_store is None:
        raise ProviderConfigurationError(
            f"LLM provider store is unavailable for model {model_id}"
        )
    provider = await llm_configuration_store.get_provider(provider_id)
    if provider is None:
        raise ProviderConfigurationError(
            f"LLM provider not found for model {model_id}: {provider_id}"
        )
    validate_provider_config(ProviderRuntimeConfig.from_row(provider))
    return provider


def _gemini_http_client(proxy: str) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        proxy=proxy,
        trust_env=False,
        timeout=httpx2.Timeout(timeout=DEFAULT_HTTP_TIMEOUT, connect=5),
        headers={"User-Agent": get_user_agent()},
    )


def _custom_openai_provider(config: ProviderRuntimeConfig) -> OpenAIProvider:
    """Build an explicit OpenAI-compatible provider for a custom endpoint."""
    return OpenAIProvider(
        base_url=effective_base_url(config),
        api_key=config.api_key,
    )


def _openai_style_provider(config: ProviderRuntimeConfig):
    use_default = config.use_default_base_url
    if config.provider_type == "deepseek":
        return (
            DeepSeekProvider(api_key=config.api_key)
            if use_default
            else _custom_openai_provider(config)
        )
    if config.provider_type == "moonshotai":
        return (
            MoonshotAIProvider(api_key=config.api_key)
            if use_default
            else _custom_openai_provider(config)
        )
    if config.provider_type == "alibaba":
        return AlibabaProvider(
            api_key=config.api_key,
            **({} if use_default else {"base_url": effective_base_url(config)}),
        )
    if config.provider_type == "zai":
        return (
            ZaiProvider(api_key=config.api_key)
            if use_default
            else _custom_openai_provider(config)
        )
    if config.provider_type == "openrouter":
        return (
            OpenRouterProvider(api_key=config.api_key)
            if use_default
            else _custom_openai_provider(config)
        )
    if config.provider_type == "openai":
        return OpenAIProvider(
            api_key=config.api_key,
            **({} if use_default else {"base_url": effective_base_url(config)}),
        )
    if config.provider_type == "openai_compatible":
        return OpenAIProvider(
            api_key=config.api_key,
            base_url=effective_base_url(config),
        )
    raise UnknownProviderTypeError(config.provider_type)


def resolve_provider_model(
    *,
    model_name: str,
    provider: ProviderRuntimeConfig,
    input_modalities: list[str] | None = None,
    tags: list[str] | None = None,
) -> Model:
    """Build a Pydantic AI model from an explicit model/provider pair."""
    definition = validate_provider_config(provider)
    model_name = model_name.strip()
    if not model_name:
        raise ProviderConfigurationError("LLM model name is required")

    if definition.model_protocol == "openai_chat":
        runtime_provider = _openai_style_provider(provider)
        model_class = (
            OpenAICompatibleVideoChatModel
            if "video" in (input_modalities or []) or "video_url" in (tags or [])
            else OpenAIChatModel
        )
        return model_class(model_name, provider=runtime_provider)
    if definition.model_protocol == "anthropic":
        runtime_provider = AnthropicProvider(
            api_key=provider.api_key,
            **(
                {}
                if provider.use_default_base_url
                else {"base_url": effective_base_url(provider)}
            ),
        )
        return AnthropicModel(model_name, provider=runtime_provider)
    if definition.model_protocol == "google":
        proxy = OPTION.agent.gemini_proxy or None
        runtime_provider = GoogleProvider(
            api_key=provider.api_key,
            http_client=_gemini_http_client(proxy) if proxy else None,
            **(
                {}
                if provider.use_default_base_url
                else {"base_url": effective_base_url(provider)}
            ),
        )
        return GoogleModel(model_name, provider=runtime_provider)
    raise ProviderConfigurationError(
        f"Unsupported model protocol for {provider.provider_type}: {definition.model_protocol}"
    )


__all__ = [
    "PROVIDER_TYPES",
    "ProviderConfigurationError",
    "ProviderRuntimeConfig",
    "ProviderRuntimeError",
    "ProviderTypeDefinition",
    "UnknownProviderTypeError",
    "effective_base_url",
    "load_provider_for_model",
    "model_unavailable_reason",
    "provider_type_definition",
    "resolve_provider_model",
    "validate_provider_config",
]
