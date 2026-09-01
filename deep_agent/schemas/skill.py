from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from deep_agent.time import UtcDatetime


class SkillResource(BaseModel):
    path: str
    category: str
    mime: str
    size: int = Field(ge=0)
    content_hash: str
    readable: bool = False
    read_format: str | None = None
    read_unit: str | None = None


class SkillPackageRecord(BaseModel):
    code: str
    label: str
    description: str
    version: str | None = None
    schema_version: str
    source: str
    platform_file_id: str
    file_name: str
    file_size: int
    content_hash: str
    manifest: dict
    resource_index: list[dict]
    contains_scripts: bool


class SkillUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, max_length=191)
    description: str | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value):
        if isinstance(value, dict):
            for field in ("label", "description"):
                if field in value and value[field] is None:
                    raise ValueError(f"{field} cannot be null")
        return value

    @field_validator("label", "description")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class SkillSummary(BaseModel):
    code: str
    label: str
    description: str
    version: str | None = None
    schema_version: str
    source: str
    reference_count: int = 0
    asset_count: int = 0
    script_count: int = 0
    contains_scripts: bool = False
    file_name: str | None = None
    file_size: int | None = None
    content_hash: str | None = None
    runtime_status: str = "missing"
    runtime_error_code: str | None = None
    runtime_synced_at: UtcDatetime | None = None
    created_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None


class SkillDetail(SkillSummary):
    manifest: dict = Field(default_factory=dict)
    resources: list[SkillResource] = Field(default_factory=list)


class SkillInstallResult(BaseModel):
    status: str
    skill: SkillDetail


class SkillReloadResult(BaseModel):
    code: str
    runtime_status: str
    runtime_error_code: str | None = None
    runtime_synced_at: UtcDatetime | None = None


class SkillResourceContent(BaseModel):
    skill_code: str
    path: str
    format: str
    unit: str
    content: str
    metadata: dict = Field(default_factory=dict)
    offset: int
    next_offset: int | None
    has_more: bool
    returned_count: int
    total_count: int
    truncated_by: str | None = None
    content_hash: str


class SkillDeleteConflict(BaseModel):
    message: str
    agents: list[str]
