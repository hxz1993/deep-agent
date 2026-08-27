from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for

from deep_agent.db.models import UiToolDefinition, UiToolPackage

if TYPE_CHECKING:
    from deep_agent.capabilities.ui.types import UIToolRecord

from deep_agent.capabilities.ui.constants import (
    MAX_DESCRIPTION_LEN,
    MAX_SCHEMA_BYTES,
    RESERVED_TOOL_NAMES,
    TOOL_NAME_PATTERN,
)

UiToolSource = Literal["builtin", "manual"]
UI_PACKAGE_CODE_PATTERN = r"^ui:[a-z][a-z0-9_-]{1,120}$"


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


def _validate_metadata_size(value: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(value, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_SCHEMA_BYTES:
        raise ValueError(f"metadata exceeds {MAX_SCHEMA_BYTES} bytes")
    return value


class UiToolCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    package_code: str = Field(pattern=UI_PACKAGE_CODE_PATTERN)
    description: str
    parameters_json_schema: dict[str, Any]
    widget: str | None = Field(default=None, max_length=64)
    result_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not TOOL_NAME_PATTERN.fullmatch(value):
            raise ValueError("name must match ^[a-z][a-z0-9_]{1,120}$")
        if value in RESERVED_TOOL_NAMES:
            raise ValueError(f"name {value!r} is reserved for builtin tools")
        return value

    @field_validator("parameters_json_schema")
    @classmethod
    def validate_parameters_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_parameters_json_schema(value)

    @field_validator("result_schema")
    @classmethod
    def validate_result_schema(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        return _validate_parameters_json_schema(value)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        return _validate_metadata_size(value)

    @model_validator(mode="after")
    def validate_payload(self) -> UiToolCreate:
        self.description = self.description.strip()
        if not self.description:
            raise ValueError("description must not be blank")
        if len(self.description) > MAX_DESCRIPTION_LEN:
            raise ValueError(f"description exceeds {MAX_DESCRIPTION_LEN} characters")
        return self


class UiToolUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_code: str | None = Field(default=None, pattern=UI_PACKAGE_CODE_PATTERN)
    description: str | None = None
    parameters_json_schema: dict[str, Any] | None = None
    widget: str | None = Field(default=None, max_length=64)
    result_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_required_fields(cls, value):
        if isinstance(value, dict):
            for field in ("package_code", "description", "parameters_json_schema"):
                if field in value and value[field] is None:
                    raise ValueError(f"{field} cannot be null")
        return value

    @field_validator("parameters_json_schema")
    @classmethod
    def validate_parameters_schema(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        return _validate_parameters_json_schema(value)

    @field_validator("result_schema")
    @classmethod
    def validate_result_schema(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        return _validate_parameters_json_schema(value)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        return _validate_metadata_size(value)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("description must not be blank")
        if len(value) > MAX_DESCRIPTION_LEN:
            raise ValueError(f"description exceeds {MAX_DESCRIPTION_LEN} characters")
        return value


class UiToolResponse(BaseModel):
    name: str
    package_code: str
    description: str
    parameters_json_schema: dict[str, Any]
    widget: str | None = None
    result_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    source: UiToolSource
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_row(cls, row: UiToolDefinition) -> UiToolResponse:
        return cls(
            name=row.name,
            package_code=row.package_code,
            description=row.description,
            parameters_json_schema=row.parameters_json_schema,
            widget=row.widget,
            result_schema=row.result_schema,
            metadata=row.metadata_,
            source=row.source,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @classmethod
    def from_record(cls, record: "UIToolRecord") -> UiToolResponse:
        return cls(
            name=record.name,
            package_code=record.package_code,
            description=record.description,
            parameters_json_schema=record.parameters_json_schema,
            widget=record.widget,
            result_schema=record.result_schema,
            metadata=record.metadata,
            source=record.source,
            created_at=None,
            updated_at=None,
        )


class UiToolListResponse(BaseModel):
    items: list[UiToolResponse]
    total: int


class UiToolPackageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=UI_PACKAGE_CODE_PATTERN)
    label: str = Field(min_length=1, max_length=191)
    description: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def normalize_text(self) -> UiToolPackageCreate:
        self.label = self.label.strip()
        if not self.label:
            raise ValueError("label must not be blank")
        if self.description is not None:
            self.description = self.description.strip() or None
        return self


class UiToolPackageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, max_length=191)
    description: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="before")
    @classmethod
    def reject_null_required_package_fields(cls, value):
        if isinstance(value, dict):
            for field in ("label",):
                if field in value and value[field] is None:
                    raise ValueError(f"{field} cannot be null")
        return value

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("label must not be blank")
        return value


class UiToolPackageResponse(BaseModel):
    code: str
    label: str
    description: str | None = None
    source: UiToolSource
    tool_count: int = 0
    tools: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_row(
        cls, row: UiToolPackage, *, tools: list[str] | None = None
    ) -> UiToolPackageResponse:
        names = tools or []
        return cls(
            code=row.code,
            label=row.label,
            description=row.description,
            source=row.source,
            tool_count=len(names),
            tools=names,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class UiToolPackageListResponse(BaseModel):
    items: list[UiToolPackageResponse]
    total: int
