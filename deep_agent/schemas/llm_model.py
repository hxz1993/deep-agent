from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import AnyHttpUrl, BaseModel, Field, TypeAdapter, field_validator, model_validator

if TYPE_CHECKING:
    from deep_agent.db.models import LlmModel

ModalitiesMode = Literal["auto", "manual"]
_HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)
LLM_REQUIRED_FIELDS = ("name", "model")


class LlmModelCreate(BaseModel):
    name: str = Field(..., max_length=191, description="Unique display name for this model preset.")
    description: str | None = Field(default=None, description="Optional human-readable description.")
    model: str = Field(..., description='Model string, e.g. "openai:gpt-4o".')
    base_url: str | None = Field(default=None, description="Optional OpenAI-compatible base URL.")
    api_key: str | None = Field(default=None, description="Optional API key for the model endpoint.")
    input_modalities: list[str] = Field(
        default_factory=list,
        description=(
            "LLM-native input modalities only (e.g. text, image, audio, video, pdf). "
            "Does not include platform upload extraction or agent tools."
        ),
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


class LlmModelUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=191)
    description: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    modalities_mode: ModalitiesMode | None = Field(
        default=None,
        description='Set to "auto" to clear manual modalities; "manual" with input_modalities sets user override.',
    )
    input_modalities: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_required_fields(cls, value):
        if isinstance(value, dict):
            for field in LLM_REQUIRED_FIELDS:
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


class LlmModelResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    model: str
    base_url: str | None = None
    api_key_set: bool = Field(description="Whether an API key is configured (value is never returned).")
    input_modalities: list[str] = Field(default_factory=list, description="Supported input modalities.")
    capabilities_source: str | None = Field(default=None, description="Source of capabilities data.")
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
            input_modalities=row.input_modalities or [],
            capabilities_source=row.capabilities_source,
            agent_count=agent_count,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class LlmModelSummary(BaseModel):
    id: str
    name: str
    model: str
    input_modalities: list[str] = Field(default_factory=list, description="Supported input modalities.")

    @classmethod
    def from_row(cls, row: LlmModel) -> LlmModelSummary:
        return cls(
            id=row.id,
            name=row.name,
            model=row.model,
            input_modalities=row.input_modalities or [],
        )
