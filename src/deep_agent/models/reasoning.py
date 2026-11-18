"""Provider-specific effort options and request-local settings."""
from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from deep_agent.models.provider_registry import model_protocol, provider_type_definition

DEFAULT_EFFORT = "default"
_EFFORT_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}\Z")
_UNSELECTABLE = {"default", "off", "disabled", "none", "on", "enabled"}


class ReasoningEffortError(ValueError):
    pass


class EffortOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["effort"]
    values: list[str] = Field(min_length=1, max_length=32)

    @field_validator("values")
    @classmethod
    def validate_values(cls, values: list[str]) -> list[str]:
        if any(not _EFFORT_PATTERN.fullmatch(value) or value.lower() == DEFAULT_EFFORT for value in values):
            raise ValueError("无效的思考档位，默认不属于模型原生档位")
        return list(dict.fromkeys(values))


class ReasoningCatalogReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str = Field(min_length=1, max_length=64)
    provider_model_id: str = Field(min_length=1, max_length=191)


def catalog_effort_options(raw) -> list[dict] | None:
    if not isinstance(raw, list):
        return None
    options = []
    for option in raw:
        if isinstance(option, dict) and option.get("type") == "effort" and isinstance(option.get("values"), list):
            values = list(dict.fromkeys(
                v for v in option["values"]
                if isinstance(v, str) and _EFFORT_PATTERN.fullmatch(v) and v.lower() != DEFAULT_EFFORT
            ))
            if values:
                options.append({"type": "effort", "values": values[:32]})
    return options[:8]


def catalog_row_options(row) -> list[dict] | None:
    for metadata in (row.provider_metadata, row.capabilities, row.model_metadata):
        if isinstance(metadata, dict) and isinstance(metadata.get("reasoning_options"), list):
            return catalog_effort_options(metadata["reasoning_options"])
    return None


def reasoning_protocol(model, provider) -> str:
    return model_protocol(provider_type_definition(provider.provider_type), getattr(model, "tags", None))


def model_binding(model, provider, *, protocol: str | None = None) -> str:
    definition = provider_type_definition(provider.provider_type)
    raw_url = (definition.default_base_url if provider.use_default_base_url else provider.base_url) or ""
    url = urlsplit(raw_url.strip())
    endpoint = (url.scheme.lower(), url.hostname, url.port, url.path.rstrip("/"))
    identity = [provider.id, provider.provider_type, protocol or reasoning_protocol(model, provider), model.model, endpoint]
    return sha256(json.dumps(identity).encode()).hexdigest()


def _matches_model_binding(binding, model, provider) -> bool:
    if binding == model_binding(model, provider):
        return True
    if provider.provider_type not in {"openai", "openai_compatible"}:
        return False
    # Accept legacy Chat bindings on OpenAI and both protocol bindings on Compatible.
    alternate = "openai_chat" if reasoning_protocol(model, provider) == "openai_responses" else "openai_responses"
    return binding == model_binding(model, provider, protocol=alternate)


def model_reasoning_options(model, provider) -> list[dict] | None:
    saved = getattr(model, "reasoning_options", None)
    if not isinstance(saved, dict) or provider is None:
        return None
    try:
        if _matches_model_binding(saved.get("binding"), model, provider):
            return catalog_effort_options(saved.get("options"))
    except ValueError:
        pass
    return None


def reasoning_settings(model, provider, effort: str) -> dict | None:
    """Map an explicitly selected native value to this SDK's settings."""
    if not _EFFORT_PATTERN.fullmatch(effort) or effort.lower() in _UNSELECTABLE:
        return None
    provider_type = provider.provider_type
    protocol = reasoning_protocol(model, provider)
    name = model.model.lower()
    if provider_type == "openrouter":
        return {"extra_body": {"reasoning": {"effort": effort}}}
    if protocol in {"openai_chat", "openai_responses", "openai_codex"}:
        settings = {"openai_reasoning_effort": effort}
        if provider_type == "alibaba" and name.startswith("qwen"):
            settings["extra_body"] = {"enable_thinking": True}
        elif provider_type == "deepseek" and name.startswith(("deepseek-v4", "deepseek-flash", "deepseek-pro")):
            settings["extra_body"] = {"thinking": {"type": "enabled"}}
        elif provider_type in {"deepseek", "moonshotai"} and (
            provider_type == "deepseek" or not name.startswith("kimi-k3")
        ):
            return None
        return settings
    if protocol == "google" and effort in {"minimal", "low", "medium", "high"}:
        from pydantic_ai.profiles.google import google_model_profile
        profile = google_model_profile(model.model) or {}
        if not profile.get('google_supports_thinking_level'):
            return None
        levels = profile.get('google_thinking_levels')
        if levels and effort.upper() not in levels:
            return None
        if effort == 'minimal' and not profile.get('google_supports_minimal_thinking_level', True):
            return None
        return {"google_thinking_config": {"thinking_level": effort.upper()}}
    if provider_type == "anthropic" and effort in {"low", "medium", "high", "xhigh", "max"}:
        from pydantic_ai.profiles.anthropic import anthropic_model_profile
        profile = anthropic_model_profile(model.model) or {}
        if effort == 'xhigh' and not profile.get('anthropic_supports_xhigh_effort'):
            return None
        if profile.get("anthropic_supports_adaptive_thinking"):
            return {"anthropic_effort": effort, "anthropic_thinking": {"type": "adaptive"}}
        if profile.get("anthropic_supports_effort"):
            return {"anthropic_effort": effort}
    return None


def available_efforts(model, provider) -> list[str]:
    return list(dict.fromkeys(
        effort for option in model_reasoning_options(model, provider) or []
        for effort in option["values"] if reasoning_settings(model, provider, effort) is not None
    ))


def saved_effort(saved) -> str | None:
    value = saved.get("value") if isinstance(saved, dict) else None
    return value if isinstance(value, str) else None


def bound_agent_effort(effort: str | None, model, provider) -> dict | None:
    if effort is None or effort == DEFAULT_EFFORT:
        return None
    if model is None or provider is None or effort not in available_efforts(model, provider):
        raise ReasoningEffortError("所选模型不支持此思考档位，请改用默认或选择其他档位")
    return {"value": effort, "binding": model_binding(model, provider)}


def effective_agent_effort(agent, model, provider) -> str | None:
    if agent.model_id != getattr(model, "id", None):
        return None
    saved = getattr(agent, "reasoning_effort", None)
    value = saved_effort(saved)
    if value is not None and value in available_efforts(model, provider) and _matches_model_binding(saved.get("binding"), model, provider):
        return value
    return None
