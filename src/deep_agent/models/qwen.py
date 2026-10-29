"""Outgoing nullable-schema compatibility for the verified Qwen 3.8 family.

Keep this at the model profile boundary: tools and their original validators
retain their schemas, and the SDK still owns request/response serialization.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic_ai.profiles import InlineDefsJsonSchemaTransformer
from pydantic_ai.providers.alibaba import AlibabaProvider

_NUMBER_KEYWORDS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}
_TYPE_KEYWORDS = {
    "array": {
        "items", "prefixItems", "additionalItems", "contains", "minContains", "maxContains",
        "minItems", "maxItems", "uniqueItems", "unevaluatedItems",
    },
    "object": {
        "properties", "patternProperties", "additionalProperties", "required", "propertyNames",
        "minProperties", "maxProperties", "dependentRequired", "dependentSchemas", "dependencies",
        "unevaluatedProperties",
    },
    "string": {"minLength", "maxLength", "pattern", "format", "contentEncoding", "contentMediaType", "contentSchema"},
    "number": _NUMBER_KEYWORDS,
    "integer": _NUMBER_KEYWORDS,
    "boolean": set(),
}
_SCHEMA_MAPS = {"$defs", "definitions", "properties", "patternProperties", "dependentSchemas", "dependencies"}
_SCHEMA_LISTS = {"allOf", "anyOf", "oneOf", "prefixItems"}
_SINGLE_SCHEMAS = {
    "items", "additionalItems", "additionalProperties", "unevaluatedItems", "unevaluatedProperties",
    "contains", "propertyNames", "not", "if", "then", "else", "contentSchema",
}
_COMPLEX_COMBINATIONS = {"allOf", "anyOf", "oneOf", "not", "if", "then", "else", "$ref", "$dynamicRef"}


def _normalize_nullable(node: Any) -> None:
    if not isinstance(node, dict):
        return
    # Visit schema-valued keywords only. Defaults, enums and examples are data,
    # even if their values happen to contain keys named "type" or "properties".
    for key in _SCHEMA_MAPS:
        if isinstance(children := node.get(key), dict):
            for child in children.values():
                _normalize_nullable(child)
    for key in _SCHEMA_LISTS:
        if isinstance(children := node.get(key), list):
            for child in children:
                _normalize_nullable(child)
    for key in _SINGLE_SCHEMAS:
        child = node.get(key)
        if isinstance(child, list):  # Older tuple-style items schemas.
            for item in child:
                _normalize_nullable(item)
        else:
            _normalize_nullable(child)

    types = node.get("type")
    if not isinstance(types, list) or len(types) != 2 or "null" not in types:
        return
    non_null_types = [item for item in types if item != "null"]
    non_null = non_null_types[0] if len(non_null_types) == 1 else None
    if not isinstance(non_null, str) or non_null not in _TYPE_KEYWORDS:
        return
    if _COMPLEX_COMBINATIONS.intersection(node):
        return

    # Type-specific constraints are ignored for null in the original schema.
    # Keep them in the typed branch; enum/const and annotations remain outside
    # the union so constraints which also apply to null are not weakened.
    typed: dict[str, Any] = {"type": non_null}
    for key in tuple(node):
        if key in _TYPE_KEYWORDS[non_null]:
            typed[key] = node.pop(key)
    node.pop("type")
    node["anyOf"] = [typed, {"type": "null"}]


class QwenNullableJsonSchemaTransformer(InlineDefsJsonSchemaTransformer):
    def __init__(self, schema: dict[str, Any], *, strict: bool | None = None):
        normalized = deepcopy(schema)
        _normalize_nullable(normalized)
        # Preserve Qwen's existing SDK reference-inlining and strict handling.
        super().__init__(normalized, strict=strict)


def qwen38_chat_profile(model_name: str) -> dict[str, Any] | None:
    """Override only the verified family, after the Provider was explicitly selected."""
    name = model_name.lower()
    if name != "qwen3.8" and not name.startswith("qwen3.8-"):
        return None
    return {
        **(AlibabaProvider.model_profile(model_name) or {}),
        "json_schema_transformer": QwenNullableJsonSchemaTransformer,
    }
