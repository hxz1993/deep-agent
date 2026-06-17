from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., description="The user's message/prompt.")
    session_id: str | None = Field(
        default=None,
        description="Conversation id. If omitted, a new session id is generated.",
    )
    model: str | None = Field(
        default=None,
        description='Optional model override, e.g. "anthropic:claude-sonnet-4-5".',
    )
    capabilities: list[str] | None = Field(
        default=None,
        description="Optional subset of capability names to enable for this run.",
    )
    user: str | None = Field(default=None, description="Optional user identifier.")


class StreamChatRequest(ChatRequest):
    agent_id: str | None = Field(
        default=None,
        description="Load agent config from the database. When set, model/capabilities are ignored.",
    )


class ChatResponse(BaseModel):
    session_id: str
    output: Any
    usage: dict[str, Any] = Field(default_factory=dict)


class ToolDescription(BaseModel):
    name: str
    description: str = ""


class CapabilityDescription(BaseModel):
    name: str
    kind: str
    description: str = ""
    tools: list[ToolDescription] = Field(default_factory=list)


class SessionInfo(BaseModel):
    session_id: str
    message_count: int
    agent_id: str | None = Field(default=None, description="Agent id used for this session.")
    agent_name: str | None = Field(default=None, description="Agent display name (if known).")
    user_prompt: str | None = Field(default=None, description="First user message in the session.")
