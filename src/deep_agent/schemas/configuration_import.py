from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ConfigurationImportSource(BaseModel):
    environment: str
    exported_at: str
    source_dialect: str
    revision: str
    git_commit: str


class ConfigurationImportResponse(BaseModel):
    status: Literal["validated", "imported"]
    source: ConfigurationImportSource
    target_revision: str
    row_counts: dict[str, int]
    agent_ids: list[str] = Field(default_factory=list)
    database_primary_keys_preserved: bool = True
    session_rows_imported: int = 0
    skill_packages: int = 0
    skill_files_uploaded: int = 0
    restart_recommended: bool = False
    warnings: list[str] = Field(default_factory=list)


__all__ = [
    "ConfigurationImportResponse",
    "ConfigurationImportSource",
]
