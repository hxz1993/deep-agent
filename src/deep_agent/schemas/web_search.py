from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from deep_agent.web.settings import WebSearchCredentialSource, WebSearchProvider
from deep_agent.time import UtcDatetime


class WebSearchValueInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["native", "provider"] = "native"
    provider: WebSearchProvider | None = None
    api_key: str | None = Field(default=None, max_length=8192)
    model: str | None = Field(default=None, max_length=191)
    credential_source: WebSearchCredentialSource | None = None
    llm_provider_id: str | None = Field(default=None, max_length=64)

    @field_validator("api_key", "model", "llm_provider_id")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class WebSearchSettingsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    value: WebSearchValueInput


class WebSearchValueResponse(BaseModel):
    mode: Literal["native", "provider"]
    provider: WebSearchProvider | None = None
    model: str | None = None
    api_key_set: bool = False
    api_key_masked: str | None = None
    credential_source: WebSearchCredentialSource | None = None
    llm_provider_id: str | None = None
    llm_provider_name: str | None = None


class WebSearchSettingsResponse(BaseModel):
    key: Literal["web_search"] = "web_search"
    version: Literal[1] = 1
    value: WebSearchValueResponse
    created_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None


class WebSearchTestResponse(BaseModel):
    ok: bool
    provider: str
    message: str
    elapsed_ms: int
