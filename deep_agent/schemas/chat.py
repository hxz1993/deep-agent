from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str | None = Field(
        default=None,
        description="The user's message/prompt. Omit when resuming with deferred_tool_results only.",
    )
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
    branch_from: int | None = Field(
        default=None,
        description="branch_point from history; truncates stored messages at this index before run.",
    )


class StreamChatRequest(ChatRequest):
    agent_id: str | None = Field(
        default=None,
        description="Load agent config from the database. When set, model/capabilities are ignored.",
    )
    deferred_tool_results: list["DeferredToolResultItem"] | None = Field(
        default=None,
        description="Results for pending UI/approval tool calls from a prior run.",
    )


class DeferredToolResultItem(BaseModel):
    tool_call_id: str = Field(..., description="ID matching the pending tool_call event.")
    result: Any | None = Field(
        default=None,
        description="Return value for a deferred external/UI tool call.",
    )
    approved: bool | None = Field(
        default=None,
        description="Approval decision for a deferred approval tool call.",
    )
    denied_message: str | None = Field(
        default=None,
        description="Optional message to the model when approved is false.",
    )


class ChatResponse(BaseModel):
    session_id: str
    output: Any
    usage: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="completed", description="completed or pending (awaiting UI input).")
    deferred: dict[str, Any] | None = Field(
        default=None,
        description="Pending tool calls when status is pending.",
    )


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
    created_at: datetime | None = Field(default=None, description="Session creation time (UTC).")
    updated_at: datetime | None = Field(default=None, description="Last conversation activity time (UTC).")


class SessionListResponse(BaseModel):
    items: list[SessionInfo]
    total: int = Field(description="Total sessions matching the filters.")
    limit: int
    offset: int
