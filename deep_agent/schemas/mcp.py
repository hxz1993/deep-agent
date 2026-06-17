from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deep_agent.db.models import McpServer

McpUserTransport = Literal["url", "stdio"]
McpTransport = Literal["url", "stdio", "builtin"]


class McpUrlConfig(BaseModel):
    url: str = Field(..., description="Remote MCP server URL (SSE or streamable HTTP).")
    headers: dict[str, str] | None = Field(default=None, description="Optional HTTP headers.")


class McpStdioConfig(BaseModel):
    command: str = Field(..., description="Executable to launch the MCP server.")
    args: list[str] = Field(default_factory=list, description="Arguments passed to the command.")
    env: dict[str, str] | None = Field(default=None, description="Optional environment variables.")
    cwd: str | None = Field(default=None, description="Optional working directory.")


class McpBuiltinConfig(BaseModel):
    """Portable config for repo-shipped servers under ``mcps/<name>/server.py``."""

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
        if self.transport == "url":
            McpUrlConfig.model_validate(self.config)
        else:
            McpStdioConfig.model_validate(self.config)
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
        if self.transport is None:
            raise ValueError("transport is required when config is updated")
        if self.transport == "url":
            McpUrlConfig.model_validate(self.config)
        else:
            McpStdioConfig.model_validate(self.config)
        return self


class McpServerResponse(BaseModel):
    name: str
    transport: McpTransport
    config: dict[str, Any]
    description: str | None = None
    enabled: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: McpServer) -> McpServerResponse:
        return cls(
            name=row.name,
            transport=row.transport,  # type: ignore[arg-type]
            config=row.config,
            description=row.description,
            enabled=row.enabled,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
