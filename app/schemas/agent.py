from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.db.models import Agent


class AgentChatRequest(BaseModel):
    agent_id: UUID = Field(..., description="Agent id to load from the database.")
    message: str = Field(..., description="The user's message/prompt.")
    session_id: str | None = Field(
        default=None,
        description="Conversation id. If omitted, a new session id is generated.",
    )
    user: str | None = Field(default=None, description="Optional user identifier.")


class AgentCreate(BaseModel):
    name: str = Field(..., description="Unique agent display name.")
    model: str = Field(..., description='Model string, e.g. "openai:gpt-4o".')
    prompt: str = Field(..., description="System prompt / instructions.")
    base_url: str | None = Field(default=None, description="Optional OpenAI-compatible base URL.")
    api_key: str | None = Field(default=None, description="Optional API key for the model endpoint.")
    capabilities: list[str] | None = Field(
        default=None,
        description="Capability names to enable. Null means all registered capabilities.",
    )


class AgentUpdate(BaseModel):
    name: str | None = None
    model: str | None = None
    prompt: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    capabilities: list[str] | None = None


class AgentResponse(BaseModel):
    id: UUID
    name: str
    model: str
    prompt: str
    base_url: str | None = None
    api_key_set: bool = Field(description="Whether an API key is configured (value is never returned).")
    capabilities: list[str] | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Agent) -> AgentResponse:
        return cls(
            id=row.id,
            name=row.name,
            model=row.model,
            prompt=row.prompt,
            base_url=row.base_url,
            api_key_set=bool(row.api_key),
            capabilities=row.capabilities,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
