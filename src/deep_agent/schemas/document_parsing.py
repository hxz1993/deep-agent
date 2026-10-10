from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from deep_agent.documents.models import DocumentProvider
from deep_agent.time import UtcDatetime


class DocumentParsingValueInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["unconfigured", "provider"]
    provider: DocumentProvider | None = None
    api_key: str | None = Field(default=None, max_length=8192)

    @field_validator("api_key")
    @classmethod
    def normalize_key(cls, value):
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def validate_clear(self):
        if self.mode == "unconfigured" and (self.provider is not None or self.api_key is not None):
            raise ValueError("清空配置时不能携带供应商或凭据")
        return self


class DocumentParsingSettingsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    value: DocumentParsingValueInput


class DocumentParsingValueResponse(BaseModel):
    mode: Literal["unconfigured", "provider"]
    provider: DocumentProvider | None = None
    model: str | None = None
    api_key_set: bool = False
    api_key_masked: str | None = None
    configuration_error: str | None = None


class DocumentProviderResponse(BaseModel):
    provider: DocumentProvider
    label: str
    model: str
    formats: list[str]
    limits: str


class DocumentParsingSettingsResponse(BaseModel):
    key: Literal["document_parsing"] = "document_parsing"
    version: Literal[1] = 1
    value: DocumentParsingValueResponse
    providers: list[DocumentProviderResponse]
    created_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None


class DocumentParsingTestResponse(BaseModel):
    ok: bool
    provider: str
    message: str
    elapsed_ms: int
    error_code: str | None = None
