from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from typing import TYPE_CHECKING

from deep_agent.mcp_request_context import (
    McpRequestContextMode,
    NEW_URL_MCP_REQUEST_CONTEXT_MODE,
    resolve_mcp_request_context_mode,
)
from deep_agent.options import OPTION

if TYPE_CHECKING:
    from deep_agent.db.models import McpServer

McpUserTransport = Literal["url"]
McpTransport = Literal["url", "builtin", "stdio"]
MCP_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,190}$")
McpInspectionStatus = Literal[
    "healthy",
    "degraded",
    "unreachable",
    "unknown",
    "stale",
    "not_applicable",
]
McpCapabilityStatus = Literal["supported", "unsupported", "error", "unknown"]


def normalize_mcp_name(value: str, *, field_name: str = "name") -> str:
    normalized = value.strip()
    if not MCP_NAME_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{field_name} must match ^[A-Za-z][A-Za-z0-9_-]{{0,190}}$"
        )
    return normalized


class McpInspectionError(BaseModel):
    stage: str
    type: str
    message: str


class McpInspectionServer(BaseModel):
    name: str | None = None
    version: str | None = None
    protocol_version: str | None = None
    instructions_available: bool = False


class McpToolsInspection(BaseModel):
    status: McpCapabilityStatus = "unknown"
    count: int = 0
    error: McpInspectionError | None = None


class McpResourcesInspection(BaseModel):
    status: McpCapabilityStatus = "unknown"
    count: int = 0
    template_count: int = 0
    error: McpInspectionError | None = None


class McpInspection(BaseModel):
    status: McpInspectionStatus = "unknown"
    checked_at: datetime | None = None
    duration_ms: int | None = None
    previous_success_at: datetime | None = None
    server: McpInspectionServer = Field(default_factory=McpInspectionServer)
    tools: McpToolsInspection = Field(default_factory=McpToolsInspection)
    resources: McpResourcesInspection = Field(default_factory=McpResourcesInspection)
    error: McpInspectionError | None = None

    @classmethod
    def from_value(cls, value: Any, *, transport: str | None = None) -> "McpInspection":
        if transport == "builtin":
            return cls(status="not_applicable")
        if not isinstance(value, dict):
            return cls()
        try:
            return cls.model_validate(value)
        except Exception:
            return cls(
                error=McpInspectionError(
                    stage="stored_value",
                    type="ValidationError",
                    message="Stored MCP inspection metadata is invalid",
                )
            )


class McpValueChange(BaseModel):
    before: str | int | None
    after: str | int | None


class McpInspectionChanges(BaseModel):
    status: McpValueChange
    tool_count: McpValueChange
    resource_count: McpValueChange
    template_count: McpValueChange


class McpInspectionResult(BaseModel):
    inspection: McpInspection
    changes: McpInspectionChanges


class McpRequestContextConfig(BaseModel):
    mode: McpRequestContextMode


class McpUrlConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    url: AnyHttpUrl = Field(..., description="Remote MCP server URL (SSE or streamable HTTP).")
    headers: dict[str, str] | None = Field(default=None, description="Optional HTTP headers.")
    request_context: McpRequestContextConfig | None = Field(
        default=None,
        description="Request context policy. Missing legacy values inherit the current request.",
    )
    init_timeout: float | None = Field(
        default=None,
        gt=0,
        description="Optional positive MCP initialization timeout in seconds.",
    )


def normalize_mcp_url_config(config: dict[str, Any]) -> dict[str, Any]:
    return McpUrlConfig.model_validate(config).model_dump(mode="json", exclude_none=True)


class McpRequestContextInfo(BaseModel):
    mode: McpRequestContextMode
    configurable: bool
    inherited_headers: list[str]


class McpBuiltinConfig(BaseModel):
    """Portable config for repo-shipped servers under ``mcps/<name>/server.py``."""

    model_config = ConfigDict(extra="allow")

    env: dict[str, str] | None = Field(default=None, description="Optional environment variables.")
    cwd: str | None = Field(default=None, description="Optional working directory.")


class McpServerCreate(BaseModel):
    name: str = Field(..., description="Unique MCP server identifier.")
    transport: McpUserTransport = Field(..., description="Connection transport type.")
    config: dict[str, Any] = Field(..., description="Transport-specific connection config.")
    description: str | None = Field(default=None, description="Optional human-readable description.")
    enabled: bool = Field(default=True, description="Whether this server is loaded at runtime.")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return normalize_mcp_name(value)

    @model_validator(mode="after")
    def validate_config(self) -> McpServerCreate:
        self.config = normalize_mcp_url_config(self.config)
        if "request_context" not in self.config:
            self.config = {
                **self.config,
                "request_context": {"mode": NEW_URL_MCP_REQUEST_CONTEXT_MODE},
            }
        return self


class McpServerUpdate(BaseModel):
    transport: McpUserTransport | None = None
    config: dict[str, Any] | None = None
    description: str | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def validate_config(self) -> McpServerUpdate:
        for field in ("transport", "config", "enabled"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if self.config is None:
            return self
        # Builtin rows PATCH config without transport; API validates shape per stored row.
        if self.transport is None:
            return self
        self.config = normalize_mcp_url_config(self.config)
        return self


class McpServerRenameRequest(BaseModel):
    new_name: str = Field(..., description="New unique MCP server identifier.")

    @field_validator("new_name")
    @classmethod
    def validate_new_name(cls, value: str) -> str:
        return normalize_mcp_name(value, field_name="new_name")


class McpServerResponse(BaseModel):
    name: str
    transport: McpTransport
    config: dict[str, Any]
    description: str | None = None
    enabled: bool
    created_at: datetime
    updated_at: datetime
    request_context: McpRequestContextInfo
    inspection: McpInspection

    @classmethod
    def from_row(cls, row: McpServer) -> McpServerResponse:
        mode = resolve_mcp_request_context_mode(row.transport, row.config)
        return cls(
            name=row.name,
            transport=row.transport,  # type: ignore[arg-type]
            config=row.config,
            description=row.description,
            enabled=row.enabled,
            created_at=row.created_at,
            updated_at=row.updated_at,
            request_context=McpRequestContextInfo(
                mode=mode,
                configurable=row.transport == "url",
                inherited_headers=(
                    sorted(OPTION.agent.mcp_forward_request_headers)
                    if mode == "inherit"
                    else []
                ),
            ),
            inspection=McpInspection.from_value(
                getattr(row, "inspection", None),
                transport=row.transport,
            ),
        )


class McpServerRenameResponse(BaseModel):
    old_name: str
    new_name: str
    updated_agents: list[str]
    mcp: McpServerResponse
