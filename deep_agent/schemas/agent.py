from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator, model_validator

from deep_agent.schemas.llm_model import LlmModelSummary

if TYPE_CHECKING:
    from deep_agent.db.models import Agent, LlmModel


LEGACY_MAX_REQUESTS_MESSAGE = (
    "max_requests 已改为全局配置，请使用环境变量 DEEP_AGENT_MAX_REQUESTS"
)
AGENT_REQUIRED_FIELDS = ("name", "model_id", "prompt")


class _RejectLegacyMaxRequests(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def reject_legacy_max_requests(cls, value):
        if isinstance(value, dict) and "max_requests" in value:
            raise ValueError(LEGACY_MAX_REQUESTS_MESSAGE)
        return value


class AgentChatRequest(BaseModel):
    agent_id: str = Field(..., description="Agent id to load from the database.")
    message: str = Field(..., description="The user's message/prompt.")
    session_id: str | None = Field(
        default=None,
        description="Conversation id. If omitted, a new session id is generated.",
    )
    user: str | None = Field(default=None, description="Optional user identifier.")


class AgentCreate(_RejectLegacyMaxRequests):
    name: str = Field(..., max_length=191, description="Unique agent display name.")
    model_id: str = Field(..., description="LLM model preset id.")
    prompt: str = Field(..., description="System prompt / instructions.")
    temperature: float | None = Field(
        default=None,
        ge=0,
        le=2,
        description="Optional sampling temperature for this agent.",
    )
    capabilities: list[str] | None = Field(
        default=None,
        description="Tool package codes to enable. Null uses default packages.",
    )

    @field_validator(*AGENT_REQUIRED_FIELDS)
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class AgentUpdate(_RejectLegacyMaxRequests):
    name: str | None = Field(default=None, max_length=191)
    model_id: str | None = None
    prompt: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    capabilities: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_required_fields(cls, value):
        if isinstance(value, dict):
            for field in AGENT_REQUIRED_FIELDS:
                if field in value and value[field] is None:
                    raise ValueError(f"{field} cannot be null")
        return value

    @field_validator(*AGENT_REQUIRED_FIELDS)
    @classmethod
    def normalize_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class AgentPromptEstimateRequest(BaseModel):
    model_id: str = Field(..., description="LLM model preset used for token estimation.")
    prompt: str = Field(..., description="System prompt text to estimate.")


class AgentPromptEstimateResponse(BaseModel):
    character_count: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    estimator_source: str


class AgentResponse(BaseModel):
    id: str
    name: str
    model_id: str
    model: LlmModelSummary
    prompt: str
    temperature: float | None = None
    capabilities: list[str] | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Agent, llm_model: LlmModel) -> AgentResponse:
        return cls(
            id=row.id,
            name=row.name,
            model_id=row.model_id,
            model=LlmModelSummary.from_row(llm_model),
            prompt=row.prompt,
            temperature=row.temperature,
            capabilities=row.capabilities,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
