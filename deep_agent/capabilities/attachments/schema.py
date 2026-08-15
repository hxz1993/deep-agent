from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AttachmentReadResult(BaseModel):
    file_id: str
    name: str | None = None
    mime: str | None = None
    unit: Literal["line", "block", "row"]
    offset: int = Field(ge=1)
    limit: int = Field(ge=1)
    returned_count: int = Field(ge=0)
    total_count: int = Field(ge=0)
    content: str
    has_more: bool
    next_offset: int | None = Field(default=None, ge=1)
    truncated_by: Literal["limit", "output_size", "item_size"] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
