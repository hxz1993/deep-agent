from __future__ import annotations

import json
import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_CONTEXT_REFERENCES = 16
MAX_CONTEXT_REFERENCE_BYTES = 256 * 1024
MAX_CONTEXT_REFERENCES_BYTES = 512 * 1024
MAX_CONTEXT_REFERENCE_DEPTH = 32


def _json_depth(value: Any, depth: int = 0) -> int:
    if depth > MAX_CONTEXT_REFERENCE_DEPTH:
        return depth
    if isinstance(value, dict):
        return max((_json_depth(item, depth + 1) for item in value.values()), default=depth)
    if isinstance(value, list):
        return max((_json_depth(item, depth + 1) for item in value), default=depth)
    return depth


def _validate_json_value(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("must not contain NaN or Infinity")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("object keys must be strings")
            _validate_json_value(item)
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("must contain JSON-compatible values only")


class ContextReference(BaseModel):
    """A structured, user-supplied snapshot attached to one chat turn."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9][a-z0-9._:-]*$",
        description="Unique reference key within this request.",
    )
    type: str = Field(
        min_length=3,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$",
        description="Namespaced business context type, for example deeptable.selection.",
    )
    schema_version: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
        description="Version of the payload schema for this context type.",
    )
    label: str | None = Field(
        default=None,
        max_length=256,
        description="Human-readable name; never used as an authorization fact.",
    )
    payload: dict[str, Any] = Field(
        description="Business facts visible to the model.",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Display/source metadata persisted in history but hidden from the model.",
    )

    @field_validator("key", "type", "schema_version")
    @classmethod
    def _strip_identifier(cls, value: str) -> str:
        stripped = value.strip()
        if stripped != value:
            raise ValueError("must not contain leading or trailing whitespace")
        return value

    @field_validator("label")
    @classmethod
    def _normalize_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _validate_json_content(self) -> "ContextReference":
        _validate_json_value(self.payload)
        _validate_json_value(self.metadata)
        if _json_depth(self.payload) > MAX_CONTEXT_REFERENCE_DEPTH:
            raise ValueError(f"payload nesting exceeds {MAX_CONTEXT_REFERENCE_DEPTH}")
        if _json_depth(self.metadata) > MAX_CONTEXT_REFERENCE_DEPTH:
            raise ValueError(f"metadata nesting exceeds {MAX_CONTEXT_REFERENCE_DEPTH}")
        try:
            encoded = json.dumps(
                self.model_dump(mode="json", exclude_none=True),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("must contain JSON-compatible values only") from exc
        if len(encoded) > MAX_CONTEXT_REFERENCE_BYTES:
            raise ValueError(
                f"serialized reference exceeds {MAX_CONTEXT_REFERENCE_BYTES} bytes"
            )
        return self


def validate_context_references(
    refs: list[ContextReference] | None,
) -> list[ContextReference] | None:
    if not refs:
        return refs
    if len(refs) > MAX_CONTEXT_REFERENCES:
        raise ValueError(f"at most {MAX_CONTEXT_REFERENCES} context references are allowed")
    keys = [ref.key for ref in refs]
    if len(set(keys)) != len(keys):
        raise ValueError("context reference keys must be unique within a request")
    encoded = json.dumps(
        [ref.model_dump(mode="json", exclude_none=True) for ref in refs],
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_CONTEXT_REFERENCES_BYTES:
        raise ValueError(
            f"serialized context_refs exceeds {MAX_CONTEXT_REFERENCES_BYTES} bytes"
        )
    return refs
