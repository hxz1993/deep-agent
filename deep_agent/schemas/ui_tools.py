from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for

from deep_agent.db.models import UiToolDefinition

if TYPE_CHECKING:
    from deep_agent.capabilities.ui.types import UIToolRecord

from deep_agent.capabilities.ui.constants import (
    MAX_DESCRIPTION_LEN,
    MAX_SCHEMA_BYTES,
    RESERVED_TOOL_NAMES,
    TOOL_NAME_PATTERN,
)

UiToolSource = Literal["builtin", "manual"]


def _validate_parameters_json_schema(value: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(value, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_SCHEMA_BYTES:
        raise ValueError(f"parameters_json_schema exceeds {MAX_SCHEMA_BYTES} bytes")
    try:
        validator_class = validator_for(value)
        validator_class.check_schema(value)
    except (SchemaError, TypeError) as exc:
        raise ValueError(f"parameters_json_schema is not a valid JSON Schema: {exc}") from exc
    return value


class UiToolCreate(BaseModel):
    name: str
    description: str
    parameters_json_schema: dict[str, Any]
    widget: str | None = Field(default=None, max_length=64)
    result_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not TOOL_NAME_PATTERN.fullmatch(value):
            raise ValueError("name must match ^[a-z][a-z0-9_]{1,120}$")
        if value in RESERVED_TOOL_NAMES and value in {"confirm_action", "ask_user_choice"}:
            raise ValueError(f"name {value!r} is reserved for builtin tools")
        return value

    @field_validator("parameters_json_schema")
    @classmethod
    def validate_parameters_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_parameters_json_schema(value)

    @model_validator(mode="after")
    def validate_payload(self) -> UiToolCreate:
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

    @field_validator("parameters_json_schema")
    @classmethod
    def validate_parameters_schema(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        return _validate_parameters_json_schema(value)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is not None and len(value) > MAX_DESCRIPTION_LEN:
            raise ValueError(f"description exceeds {MAX_DESCRIPTION_LEN} characters")
        return value


class UiToolResponse(BaseModel):
    name: str
    description: str
    parameters_json_schema: dict[str, Any]
    widget: str | None = None
    result_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    source: UiToolSource
    enabled: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_row(cls, row: UiToolDefinition) -> UiToolResponse:
        return cls(
            name=row.name,
            description=row.description,
            parameters_json_schema=row.parameters_json_schema,
            widget=row.widget,
            result_schema=row.result_schema,
            metadata=row.metadata_,
            source=row.source,
            enabled=row.enabled,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @classmethod
    def from_record(cls, record: "UIToolRecord") -> UiToolResponse:
        return cls(
            name=record.name,
            description=record.description,
            parameters_json_schema=record.parameters_json_schema,
            widget=record.widget,
            result_schema=record.result_schema,
            metadata=record.metadata,
            source=record.source,
            enabled=record.enabled,
            created_at=None,
            updated_at=None,
        )


class UiToolListResponse(BaseModel):
    items: list[UiToolResponse]
    total: int
