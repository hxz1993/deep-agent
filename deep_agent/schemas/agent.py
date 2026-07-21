from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from deep_agent.schemas.llm_model import LlmModelSummary

if TYPE_CHECKING:
    from deep_agent.db.models import Agent, LlmModel


class AgentChatRequest(BaseModel):
    agent_id: str = Field(..., description="Agent id to load from the database.")
    message: str = Field(..., description="The user's message/prompt.")
    session_id: str | None = Field(
        default=None,
        description="Conversation id. If omitted, a new session id is generated.",
    )
    user: str | None = Field(default=None, description="Optional user identifier.")


class AgentCreate(BaseModel):
    name: str = Field(..., description="Unique agent display name.")
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
        description="Capability names to enable. Null uses default capabilities and no extension UI tools.",
    )
    max_requests: int | None = Field(
        default=None,
        ge=1,
        description="Max model requests per turn. Null uses DEEP_AGENT_MAX_REQUESTS // 2.",
    )


class AgentUpdate(BaseModel):
    name: str | None = None
    model_id: str | None = None
    prompt: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    capabilities: list[str] | None = None
    max_requests: int | None = Field(default=None, ge=1)


class AgentResponse(BaseModel):
    id: str
    name: str
    model_id: str
    model: LlmModelSummary
    prompt: str
    temperature: float | None = None
    capabilities: list[str] | None = None
    max_requests: int | None = Field(
        default=None,
        description="Configured max requests per turn. Null means global default (DEEP_AGENT_MAX_REQUESTS // 2).",
    )
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
            max_requests=row.max_requests,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
