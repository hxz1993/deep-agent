from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from deep_agent.capabilities.codes import canonical_tool_package_codes
from deep_agent.capabilities.ui.selection import normalize_ui_tool_selection
from deep_agent.schemas.llm_model import LlmModelSummary
from deep_agent.time import UtcDatetime

if TYPE_CHECKING:
    from deep_agent.db.models import Agent, LlmModel


LEGACY_MAX_REQUESTS_MESSAGE = (
    "max_requests 已改为全局配置，请使用环境变量 DEEP_AGENT_MAX_REQUESTS"
)
AGENT_REQUIRED_FIELDS = ("name", "prompt")


class _RejectRemovedAgentFields(BaseModel):
    @field_validator("ui_tool_selection", check_fields=False)
    @classmethod
    def validate_ui_tool_scope(cls, value):
        return normalize_ui_tool_selection(value) if value is not None else value

    @model_validator(mode="before")
    @classmethod
    def reject_removed_fields(cls, value):
        if isinstance(value, dict) and "temperature" in value:
            raise ValueError("temperature 已移除，模型使用服务端默认采样参数")
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


class AgentCreate(_RejectRemovedAgentFields):
    name: str = Field(..., max_length=191, description="Unique agent display name.")
    model_id: str | None = Field(default=None, description="Optional LLM model preset id.")
    prompt: str = Field(..., description="System prompt / instructions.")
    capabilities: list[str] = Field(
        default_factory=list,
        description="Tool package codes explicitly associated with this agent.",
    )
    ui_tool_selection: dict[str, list[str]] = Field(default_factory=dict)
    skills: list[str] = Field(
        default_factory=list,
        description="Skill package codes explicitly associated with this agent.",
    )

    @field_validator(*AGENT_REQUIRED_FIELDS, "model_id")
    @classmethod
    def normalize_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("capabilities", "skills")
    @classmethod
    def validate_code_list(cls, value: list[str], info: ValidationInfo) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("values cannot contain blank strings")
        if len(set(normalized)) != len(normalized):
            raise ValueError("values must be unique")
        return (
            canonical_tool_package_codes(normalized)
            if info.field_name == "capabilities"
            else normalized
        )


class AgentUpdate(_RejectRemovedAgentFields):
    name: str | None = Field(default=None, max_length=191)
    model_id: str | None = None
    prompt: str | None = None
    capabilities: list[str] | None = None
    skills: list[str] | None = None
    ui_tool_selection: dict[str, list[str]] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_required_fields(cls, value):
        if isinstance(value, dict):
            for field in AGENT_REQUIRED_FIELDS:
                if field in value and value[field] is None:
                    raise ValueError(f"{field} cannot be null")
            if "ui_tool_selection" in value and value["ui_tool_selection"] is None:
                raise ValueError("ui_tool_selection cannot be null")
            if "skills" in value and value["skills"] is None:
                raise ValueError("skills cannot be null")
            if "capabilities" in value and value["capabilities"] is None:
                raise ValueError("capabilities cannot be null")
        return value

    @field_validator(*AGENT_REQUIRED_FIELDS, "model_id")
    @classmethod
    def normalize_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("capabilities", "skills")
    @classmethod
    def validate_code_list(
        cls,
        value: list[str] | None,
        info: ValidationInfo,
    ) -> list[str] | None:
        if value is None:
            return None
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("values cannot contain blank strings")
        if len(set(normalized)) != len(normalized):
            raise ValueError("values must be unique")
        return (
            canonical_tool_package_codes(normalized)
            if info.field_name == "capabilities"
            else normalized
        )


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
    model_id: str | None
    model: LlmModelSummary | None
    prompt: str
    capabilities: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    ui_tool_selection: dict[str, list[str]] = Field(default_factory=dict)
    created_at: UtcDatetime
    updated_at: UtcDatetime

    @classmethod
    def from_row(cls, row: Agent, llm_model: LlmModel | None) -> AgentResponse:
        return cls(
            id=row.id,
            name=row.name,
            model_id=row.model_id,
            model=LlmModelSummary.from_row(llm_model) if llm_model is not None else None,
            prompt=row.prompt,
            capabilities=canonical_tool_package_codes(row.capabilities),
            skills=row.skills or [],
            ui_tool_selection=getattr(row, "ui_tool_selection", None) or {},
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
