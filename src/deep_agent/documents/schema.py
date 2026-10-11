from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from deep_agent.files.schema import AttachmentFileRef, SkillFileRef
from .export import ExportResult


class DocumentRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["document"] = "document"
    document_id: str = Field(min_length=1)


DocumentResource = Annotated[
    AttachmentFileRef | SkillFileRef | DocumentRef, Field(discriminator="type")
]


class PageFragment(BaseModel):
    page_number: int | None
    format: str
    content: str
    char_offset: int
    char_end: int
    complete: bool
    layout: dict[str, Any] | None = None


class DocumentSummary(BaseModel):
    source_pages: int | None
    parsed_pages: int
    page_numbers_known: bool
    missing_pages: list[int]
    coverage: Literal["complete", "partial", "unknown"]


class DocumentReadResult(BaseModel):
    document: DocumentRef
    provider: str
    model: str
    status: Literal["complete", "partial"]
    summary: DocumentSummary
    pages: list[PageFragment]
    warnings: list[str]
    offset: int
    char_offset: int
    limit: int
    returned_count: int
    has_more: bool
    next_read: dict | None
    reading_view: Literal["readable", "layout"] = "readable"
    export: ExportResult = Field(default_factory=ExportResult)
