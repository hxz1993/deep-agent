from __future__ import annotations

from pydantic import BaseModel, Field


class AttachmentRef(BaseModel):
    id: str = Field(..., description="Platform file id from POST /uploads.")
    name: str | None = Field(default=None, description="Original filename.")
    mime: str | None = Field(default=None, description="MIME type.")
    extracted_text: str | None = Field(
        default=None,
        description="Document text from upload pipeline; persisted in chat history.",
    )


class UploadResponse(BaseModel):
    id: str
    name: str
    mime: str
    size: int
    extracted_text: str | None = None
