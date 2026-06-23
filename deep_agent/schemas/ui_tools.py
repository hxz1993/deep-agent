from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from deep_agent.db.models import UiToolDefinition

from deep_agent.capabilities.ui.constants import (
    MAX_DESCRIPTION_LEN,
    MAX_SCHEMA_BYTES,
    MAX_UI_TOOLS,
    RESERVED_TOOL_NAMES,
    TOOL_NAME_PATTERN,
)

UiToolSource = Literal["builtin", "mcp", "manual"]


class UiToolManifestEntry(BaseModel):
    name: str
    description: str
    parameters_json_schema: dict[str, Any]
    widget: str | None = None
    result_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def validate_local_name(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value):
            raise ValueError("tool name must match ^[a-z][a-z0-9_]{0,63}$")
        if value in RESERVED_TOOL_NAMES:
            raise ValueError(f"tool name {value!r} is reserved")
        return value

    @model_validator(mode="after")
    def validate_schema_size(self) -> UiToolManifestEntry:
        encoded = json.dumps(self.parameters_json_schema, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > MAX_SCHEMA_BYTES:
            raise ValueError(f"parameters_json_schema exceeds {MAX_SCHEMA_BYTES} bytes")
        if len(self.description) > MAX_DESCRIPTION_LEN:
            raise ValueError(f"description exceeds {MAX_DESCRIPTION_LEN} characters")
        return self


class UiToolsManifest(BaseModel):
    protocol_version: str
    mcp_server: str | None = None
    tools: list[UiToolManifestEntry] = Field(default_factory=list)

    @field_validator("protocol_version")
    @classmethod
    def validate_protocol(cls, value: str) -> str:
        if value != "1.0":
            raise ValueError("unsupported protocol_version")
        return value

    @model_validator(mode="after")
    def validate_tool_count(self) -> UiToolsManifest:
        if len(self.tools) > MAX_UI_TOOLS:
            raise ValueError(f"too many tools (max {MAX_UI_TOOLS})")
        return self


class UiToolCreate(BaseModel):
    name: str
    description: str
    parameters_json_schema: dict[str, Any]
    widget: str | None = Field(default=None, max_length=64)
    result_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    enabled: bool = True
    override: bool = False
    agent_ids: list[str] | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not TOOL_NAME_PATTERN.fullmatch(value):
            raise ValueError("name must match ^[a-z][a-z0-9_]{1,120}$")
        if value in RESERVED_TOOL_NAMES and value in {"confirm_action", "ask_user_choice"}:
            raise ValueError(f"name {value!r} is reserved for builtin tools")
        return value

    @model_validator(mode="after")
    def validate_payload(self) -> UiToolCreate:
        encoded = json.dumps(self.parameters_json_schema, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > MAX_SCHEMA_BYTES:
            raise ValueError(f"parameters_json_schema exceeds {MAX_SCHEMA_BYTES} bytes")
        if len(self.description) > MAX_DESCRIPTION_LEN:
            raise ValueError(f"description exceeds {MAX_DESCRIPTION_LEN} characters")
        return self


class UiToolUpdate(BaseModel):
    description: str | None = None
    parameters_json_schema: dict[str, Any] | None = None
    widget: str | None = Field(default=None, max_length=64)
    result_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    enabled: bool | None = None
    override: bool | None = None
    agent_ids: list[str] | None = None


class UiToolResponse(BaseModel):
    name: str
    description: str
    parameters_json_schema: dict[str, Any]
    widget: str | None = None
    result_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    source: UiToolSource
    mcp_server_name: str | None = None
    enabled: bool
    override: bool
    agent_ids: list[str] | None = None
    discovered_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    shadowed_by: str | None = None

    @classmethod
    def from_row(cls, row: UiToolDefinition, *, shadowed_by: str | None = None) -> UiToolResponse:
        return cls(
            name=row.name,
            description=row.description,
            parameters_json_schema=row.parameters_json_schema,
            widget=row.widget,
            result_schema=row.result_schema,
            metadata=row.metadata_,
            source=row.source,
            mcp_server_name=row.mcp_server_name,
            enabled=row.enabled,
            override=row.override,
            agent_ids=row.agent_ids,
            discovered_at=row.discovered_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
            shadowed_by=shadowed_by,
        )


class UiToolListResponse(BaseModel):
    items: list[UiToolResponse]
    total: int


class UiToolsRefreshResponse(BaseModel):
    mcp_server: str
    discovered: int
    upserted: int
    removed: int
    errors: list[str] = Field(default_factory=list)
