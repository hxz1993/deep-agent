from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
McpTransport = Literal["url", "builtin"]


class McpRequestContextConfig(BaseModel):
    mode: McpRequestContextMode


class McpUrlConfig(BaseModel):
    url: str = Field(..., description="Remote MCP server URL (SSE or streamable HTTP).")
    headers: dict[str, str] | None = Field(default=None, description="Optional HTTP headers.")
    request_context: McpRequestContextConfig | None = Field(
        default=None,
        description="Request context policy. Missing legacy values inherit the current request.",
    )


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

    @model_validator(mode="after")
    def validate_config(self) -> McpServerCreate:
        McpUrlConfig.model_validate(self.config)
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
        if self.config is None:
            return self
        # Builtin rows PATCH config without transport; API validates shape per stored row.
        if self.transport is None:
            return self
        McpUrlConfig.model_validate(self.config)
        return self


class McpServerResponse(BaseModel):
    name: str
    transport: McpTransport
    config: dict[str, Any]
    description: str | None = None
    enabled: bool
    created_at: datetime
    updated_at: datetime
    request_context: McpRequestContextInfo

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
        )
