from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ConversationStatus = Literal[
    "pending",
    "awaiting_input",
    "completed",
    "failed",
    "rejected",
    "interrupted",
    "removed",
]


class ConversationOrder(BaseModel):
    sequence: int
    part_offset: int = 0


class ConversationAttachment(BaseModel):
    id: str
    name: str | None = None
    mime: str | None = None
    byte_size: int | None = None
    content_hash: str | None = None


class ConversationArtifact(BaseModel):
    artifact_id: str
    kind: str = "tool_result_overflow"
    role: str | None = None
    byte_size: int | None = None
    content_hash: str | None = None


class ConversationItemBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    kind: str
    status: ConversationStatus
    turn_id: str | None = None
    run_id: str | None = None
    order: ConversationOrder
    source_event_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class UserConversationItem(ConversationItemBase):
    kind: Literal["user_message"] = "user_message"
    text: str | None = None
    attachments: list[ConversationAttachment] = Field(default_factory=list)
    context_refs: list[dict[str, Any]] = Field(default_factory=list)


class AssistantConversationItem(ConversationItemBase):
    kind: Literal["assistant_message"] = "assistant_message"
    phase: Literal["process", "final"] = "process"
    thinking: str | None = None
    text: str | None = None
    conversation_id: str | None = None
    model_name: str | None = None
    provider_name: str | None = None
    provider_response_id: str | None = None
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None


class ToolConversationItem(ConversationItemBase):
    kind: Literal["tool"] = "tool"
    call_id: str
    tool_name: str | None = None
    mode: Literal["tool", "deferred", "tool_search", "native"] = "tool"
    input: Any = None
    output: Any = None
    outcome: str | None = None
    widget: str | None = None
    metadata: dict[str, Any] | None = None
    artifacts: list[ConversationArtifact] = Field(default_factory=list)


class PlanConversationItem(ConversationItemBase):
    kind: Literal["plan"] = "plan"
    call_id: str
    tool_name: Literal["write_plan"] = "write_plan"
    input: Any = None
    output: Any = None
    plan: dict[str, Any] | None = None
    total: int = 0
    completed: int = 0


ConversationItem = (
    UserConversationItem
    | AssistantConversationItem
    | ToolConversationItem
    | PlanConversationItem
)


class ConversationHistoryResponse(BaseModel):
    schema_version: Literal[1] = 1
    session_id: str
    event_head_sequence: int = 0
    items: list[ConversationItem] = Field(default_factory=list)
