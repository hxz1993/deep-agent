from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.schemas.context_reference import ContextReference, validate_context_references
from deep_agent.time import UtcDatetime


class ChatRequest(BaseModel):
    agent_id: str | None = Field(
        default=None,
        description=(
            "Agent configuration id. Required for a new session; an existing "
            "session resolves and verifies its stored Agent automatically."
        ),
    )
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

    capabilities: list[str] | None = Field(
        default=None,
        description="Optional tool package codes to enable for this run.",
    )
    branch_from: int | None = Field(
        default=None,
        description="Reserved. Session Event Store branching is not implemented in v0.6.0.",
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
    status: str = Field(default="completed", description="completed, pending (awaiting UI input), or cancelled.")
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


class ToolPackageDescription(BaseModel):
    code: str
    category: str
    label: str | None = None
    description: str = ""
    tools: list[ToolDescription] = Field(default_factory=list)
    source: str | None = None
    tool_count: int = 0
    resource_count: int = 0
    tool_catalog_status: str = "complete"
    disclosure_mode: Literal["direct", "conditional", "search"] = "direct"
    disclosure_condition: str | None = None
    preselect_limit: int | None = None
    search_result_limit: int | None = None


class ChannelSessionOrigin(BaseModel):
    """Channel details for an explicitly channel-sourced session."""

    account_id: str
    account_name: str
    channel_type: str
    peer_type: str | None = None
    peer_id: str | None = None
    account_deleted: bool = False
    peer_metadata: dict[str, str] = Field(default_factory=dict)


class SessionInfo(BaseModel):
    source_type: str = Field(default="internal", description="Immutable creation source; legacy NULL is internal.")
    source_id: str | None = Field(default=None, description="Source instance ID, not necessarily a channel account.")
    app_id: str | None = Field(default=None, description="App recorded when the session was created.")
    space_id: str | None = Field(default=None, description="Space recorded when the session was created.")
    session_id: str
    message_count: int
    turn_count: int = Field(default=0, description="Distinct conversation turns in this session.")
    attachment_count: int = Field(default=0, description="Attachment references in user messages.")
    agent_id: str | None = Field(default=None, description="Agent id used for this session.")
    agent_name: str | None = Field(default=None, description="Agent display name (if known).")
    user_prompt: str | None = Field(default=None, description="First user message in the session.")
    title: str | None = Field(default=None, description="Session title (automatic, channel-derived, or user-defined).")
    channel: ChannelSessionOrigin | None = Field(default=None, description="External channel origin, if this is a channel-created session.")
    created_at: UtcDatetime | None = Field(default=None, description="Session creation time (UTC).")
    updated_at: UtcDatetime | None = Field(default=None, description="Last conversation activity time (UTC).")


class SessionListResponse(BaseModel):
    items: list[SessionInfo]
    total: int = Field(description="Total sessions matching the filters.")
    limit: int
    offset: int
