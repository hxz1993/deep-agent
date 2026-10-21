from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationInfo, field_validator, model_validator

from deep_agent.capabilities.codes import canonical_tool_package_codes
from deep_agent.schemas.llm_model import LlmModelSummary
from deep_agent.time import UtcDatetime

if TYPE_CHECKING:
    from deep_agent.db.models import Agent, LlmModel


LEGACY_MAX_REQUESTS_MESSAGE = (
    "max_requests 已改为全局配置，请使用环境变量 DEEP_AGENT_MAX_REQUESTS"
)
AGENT_REQUIRED_FIELDS = ("name", "prompt")


class _RejectRemovedAgentFields(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def reject_removed_fields(cls, value):
        if isinstance(value, dict) and "temperature" in value:
            raise ValueError("temperature 已移除，模型使用服务端默认采样参数")
        if isinstance(value, dict) and "max_requests" in value:
            raise ValueError(LEGACY_MAX_REQUESTS_MESSAGE)
        if isinstance(value, dict) and ("updated_by" in value or "updated_by_user" in value):
            raise ValueError("updated_by is read-only and comes from the authenticated operator")
        return value


class AgentOutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    description: str | None = Field(default=None, max_length=8192)
    json_schema: dict

    @field_validator("json_schema")
    @classmethod
    def validate_schema(cls, value: dict) -> dict:
        from deep_agent.structured_output.schema import normalize_schema
        return normalize_schema(value)


class AgentOutputConfigResponse(BaseModel):
    structured_output_enabled: bool
    output_config: AgentOutputConfig | None


class AgentUpdater(BaseModel):
    id: str
    username: str | None = None
    resolution: str


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
    skills: list[str] = Field(
        default_factory=list,
        description="Skill package codes explicitly associated with this agent.",
    )
    structured_output_enabled: StrictBool = False
    output_config: AgentOutputConfig | None = None

    @model_validator(mode="after")
    def require_output_definition(self):
        if self.structured_output_enabled and self.output_config is None:
            raise ValueError("output_config is required when structured output is enabled")
        return self

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
    structured_output_enabled: StrictBool | None = None
    output_config: AgentOutputConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_required_fields(cls, value):
        if isinstance(value, dict):
            for field in AGENT_REQUIRED_FIELDS:
                if field in value and value[field] is None:
                    raise ValueError(f"{field} cannot be null")
            if "skills" in value and value["skills"] is None:
                raise ValueError("skills cannot be null")
            if "capabilities" in value and value["capabilities"] is None:
                raise ValueError("capabilities cannot be null")
            if "structured_output_enabled" in value and value["structured_output_enabled"] is None:
                raise ValueError("structured_output_enabled cannot be null")
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
    structured_output_enabled: bool = False
    updated_by: str | None = None
    updated_by_user: AgentUpdater | None = None
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
            structured_output_enabled=bool(row.structured_output_enabled),
            updated_by=row.updated_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
