from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import AnyHttpUrl, BaseModel, Field, TypeAdapter, field_validator, model_validator

if TYPE_CHECKING:
    from deep_agent.db.models import LlmModel

_HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)
_TAG_PATTERN = re.compile(r"^[a-z][a-z0-9_:-]{0,63}$")
_ALLOWED_MODALITIES = {"text", "image", "audio", "video"}
LLM_REQUIRED_FIELDS = ("name", "model")


def _supported_modalities(values: list[str] | None) -> list[str]:
    """Hide retired modality values from historical database rows."""
    return [value for value in (values or []) if value in _ALLOWED_MODALITIES]


def _normalize_list(values: list[str], *, field_name: str) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw).strip().lower()
        if not value:
            continue
        if field_name == "input_modalities" and value not in _ALLOWED_MODALITIES:
            raise ValueError(f"unsupported input modality: {value}")
        if field_name == "tags" and not _TAG_PATTERN.fullmatch(value):
            raise ValueError(f"invalid tag: {value}")
        if value not in seen:
            seen.add(value)
            normalized.append(value)
    return normalized


def _validate_token_pair(context_window_tokens: int | None, max_output_tokens: int | None) -> None:
    if (
        context_window_tokens is not None
        and max_output_tokens is not None
        and max_output_tokens > context_window_tokens
    ):
        raise ValueError("max_output_tokens must not exceed context_window_tokens")


class LlmModelCreate(BaseModel):
    name: str = Field(..., max_length=191, description="Unique display name for this model preset.")
    description: str | None = Field(default=None, description="Optional human-readable description.")
    model: str = Field(..., description='Runtime model string, e.g. "openai:gpt-4o".')
    base_url: str | None = Field(default=None, description="Optional provider endpoint URL.")
    api_key: str | None = Field(default=None, description="Optional API key for the model endpoint.")
    input_modalities: list[str] = Field(
        ...,
        min_length=1,
        description="LLM-native input modalities only: text, image, audio, or video.",
    )
    context_window_tokens: int = Field(
        ...,
        gt=0,
        description="Provider-declared shared input and output context window.",
    )
    max_output_tokens: int = Field(
        ...,
        gt=0,
        description="Provider-declared maximum output tokens; not a fixed runtime reserve.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Normalized model capability tags, separate from input modalities.",
    )
    @field_validator(*LLM_REQUIRED_FIELDS)
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("description", "api_key")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return str(_HTTP_URL_ADAPTER.validate_python(value.strip())).rstrip("/")

    @field_validator("input_modalities")
    @classmethod
    def normalize_modalities(cls, value: list[str]) -> list[str]:
        normalized = _normalize_list(value, field_name="input_modalities")
        if not normalized:
            raise ValueError("at least one input modality is required")
        return normalized

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        return _normalize_list(value, field_name="tags")

    @model_validator(mode="after")
    def validate_token_limits(self) -> LlmModelCreate:
        _validate_token_pair(self.context_window_tokens, self.max_output_tokens)
        return self


class LlmModelUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=191)
    description: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    input_modalities: list[str] | None = None
    context_window_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    tags: list[str] | None = None
    @model_validator(mode="before")
    @classmethod
    def reject_null_required_fields(cls, value):
        if isinstance(value, dict):
            for field in (
                *LLM_REQUIRED_FIELDS,
                "input_modalities",
                "context_window_tokens",
                "max_output_tokens",
            ):
                if field in value and value[field] is None:
                    raise ValueError(f"{field} cannot be null")
        return value

    @field_validator(*LLM_REQUIRED_FIELDS)
    @classmethod
    def normalize_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("description", "api_key")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return str(_HTTP_URL_ADAPTER.validate_python(value.strip())).rstrip("/")

    @field_validator("input_modalities")
    @classmethod
    def normalize_modalities(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = _normalize_list(value, field_name="input_modalities")
        if not normalized:
            raise ValueError("at least one input modality is required")
        return normalized

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _normalize_list(value, field_name="tags")

    @model_validator(mode="after")
    def validate_token_limits(self) -> LlmModelUpdate:
        _validate_token_pair(self.context_window_tokens, self.max_output_tokens)
        return self


class LlmModelResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    model: str
    base_url: str | None = None
    api_key_set: bool = Field(description="Whether an API key is configured (value is never returned).")
    input_modalities: list[str] = Field(default_factory=list, description="Supported input modalities.")
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    tags: list[str] = Field(default_factory=list)
    agent_count: int = Field(default=0, description="Number of agents referencing this preset.")
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: LlmModel, *, agent_count: int = 0) -> LlmModelResponse:
        return cls(
            id=row.id,
            name=row.name,
            description=row.description,
            model=row.model,
            base_url=row.base_url,
            api_key_set=bool(row.api_key),
            input_modalities=_supported_modalities(row.input_modalities),
            context_window_tokens=row.context_window_tokens,
            max_output_tokens=row.max_output_tokens,
            tags=row.tags or [],
            agent_count=agent_count,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class LlmModelSummary(BaseModel):
    id: str
    name: str
    model: str
    input_modalities: list[str] = Field(default_factory=list)
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    tags: list[str] = Field(default_factory=list)

    @classmethod
    def from_row(cls, row: LlmModel) -> LlmModelSummary:
        return cls(
            id=row.id,
            name=row.name,
            model=row.model,
            input_modalities=_supported_modalities(row.input_modalities),
            context_window_tokens=row.context_window_tokens,
            max_output_tokens=row.max_output_tokens,
            tags=row.tags or [],
        )
