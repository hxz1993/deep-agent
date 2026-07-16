from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deep_agent.db.models import McpServer

McpUserTransport = Literal["url"]
McpTransport = Literal["url", "builtin"]


class McpUrlConfig(BaseModel):
    url: str = Field(..., description="Remote MCP server URL (SSE or streamable HTTP).")
    headers: dict[str, str] | None = Field(default=None, description="Optional HTTP headers.")


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
