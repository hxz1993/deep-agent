from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.schemas.context_reference import ContextReference, validate_context_references


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
        description='Optional model override, e.g. "anthropic:claude-sonnet-4-5". Ignored when model_id is set.',
    )
    model_id: str | None = Field(
        default=None,
        description="Optional LLM preset id. With agent_id, overrides the agent default for this request only.",
    )
    temperature: float | None = Field(
        default=None,
        ge=0,
        le=2,
        description="Optional sampling temperature. Ignored when agent_id is set.",
    )
    capabilities: list[str] | None = Field(
        default=None,
        description="Optional tool package codes to enable for this run.",
    )
    branch_from: int | None = Field(
        default=None,
        description="branch_point from history; truncates stored messages at this index before run.",
    )
    attachments: list[AttachmentRef] | None = Field(
        default=None,
        description="Uploaded file references from POST /api/v1/uploads.",
    )
    context_refs: list[ContextReference] | None = Field(
        default=None,
        description="Structured page/business context snapshots for this user turn.",
    )

    @field_validator("context_refs")
    @classmethod
    def _validate_context_refs(
        cls,
        value: list[ContextReference] | None,
    ) -> list[ContextReference] | None:
        return validate_context_references(value)


class StreamChatRequest(ChatRequest):
    agent_id: str | None = Field(
        default=None,
        description="Load agent config from the database. model_id may override the agent's LLM preset per request.",
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
    conversation_id: str | None = None
    run_id: str | None = None
    output: Any
    usage: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="completed", description="completed or pending (awaiting UI input).")
    deferred: dict[str, Any] | None = Field(
        default=None,
        description="Pending tool calls when status is pending.",
    )
    context: dict[str, Any] | None = Field(
        default=None,
        description="Read-only context budget and compaction telemetry for this run.",
    )
    run_metrics: dict[str, Any] | None = Field(
        default=None,
        description="Ephemeral metrics for the just-completed run.",
    )


class ToolDescription(BaseModel):
    name: str
    display_name: str
    description: str = ""
    execution_type: str
    enabled: bool = True


class ToolPackageDescription(BaseModel):
    code: str
    category: str
    label: str | None = None
    description: str = ""
    tools: list[ToolDescription] = Field(default_factory=list)
    enabled: bool = True
    default_enabled: bool = False
    source: str | None = None
    tool_count: int = 0
    tool_catalog_status: str = "complete"
    disclosure_mode: Literal["direct", "conditional", "search"] = "direct"
    disclosure_condition: str | None = None
    preselect_limit: int | None = None
    search_result_limit: int | None = None


class SessionInfo(BaseModel):
    session_id: str
    message_count: int
    agent_id: str | None = Field(default=None, description="Agent id used for this session.")
    agent_name: str | None = Field(default=None, description="Agent display name (if known).")
    user_prompt: str | None = Field(default=None, description="First user message in the session.")
    title: str | None = Field(default=None, description="Session title (generated from first user message).")
    created_at: datetime | None = Field(default=None, description="Session creation time (UTC).")
    updated_at: datetime | None = Field(default=None, description="Last conversation activity time (UTC).")


class SessionListResponse(BaseModel):
    items: list[SessionInfo]
    total: int = Field(description="Total sessions matching the filters.")
    limit: int
    offset: int
