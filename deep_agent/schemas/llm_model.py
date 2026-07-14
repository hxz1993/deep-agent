from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from deep_agent.db.models import LlmModel

ModalitiesMode = Literal["auto", "manual"]


class LlmModelCreate(BaseModel):
    name: str = Field(..., description="Unique display name for this model preset.")
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


class LlmModelUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    modalities_mode: ModalitiesMode | None = Field(
        default=None,
        description='Set to "auto" to clear manual modalities; "manual" with input_modalities sets user override.',
    )
    input_modalities: list[str] | None = None


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
