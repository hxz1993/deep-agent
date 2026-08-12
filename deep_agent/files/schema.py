from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AttachmentFileRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["attachment"]
    file_id: str = Field(min_length=1)


class SkillFileRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["skill"]
    skill_code: str = Field(min_length=1)
    path: str = Field(min_length=1)


FileResourceRef = Annotated[
    AttachmentFileRef | SkillFileRef,
    Field(discriminator="type"),
]


class FileReadResult(BaseModel):
    source: Literal["attachment", "skill"]
    resource: AttachmentFileRef | SkillFileRef
    name: str
    mime: str | None = None
    format: Literal["text", "docx", "xlsx", "image"]
    unit: Literal["line", "block", "row", "image"]
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    offset: int = Field(ge=1)
    limit: int = Field(ge=1)
    returned_count: int = Field(ge=0)
    total_count: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=1)
    has_more: bool
    truncated_by: Literal["limit", "output_size", "item_size"] | None = None
    content_hash: str
