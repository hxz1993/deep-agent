from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

DocumentProvider = Literal["paddle", "glm"]


@dataclass(frozen=True)
class ProviderCapability:
    provider: DocumentProvider
    label: str
    model: str
    extensions: tuple[str, ...]
    mime_types: frozenset[str]
    limits: str
    max_bytes: int
    max_pages: int | None = None

    def supports(self, name: str, mime: str) -> bool:
        return Path(name).suffix.lower() in self.extensions and mime.split(";", 1)[0].strip().lower() in self.mime_types

    def describe(self) -> dict[str, Any]:
        return {"provider": self.provider, "label": self.label, "model": self.model,
                "formats": [ext[1:].upper() for ext in self.extensions], "limits": self.limits}


@dataclass(frozen=True)
class DocumentSource:
    name: str
    mime_type: str
    data: bytes
    source_pages: int | None = None


@dataclass(frozen=True)
class DocumentPage:
    page: int | None
    content: str
    format: str = "markdown"
    native: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedDocument:
    provider: str
    model: str
    pages: list[DocumentPage]
    source_pages: int | None
    status: Literal["complete", "partial"] = "complete"
    warnings: list[str] = field(default_factory=list)
    native: dict[str, Any] = field(default_factory=dict)


class DocumentParsingError(ValueError):
    """Safe category/message; upstream exception text must never be surfaced."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
