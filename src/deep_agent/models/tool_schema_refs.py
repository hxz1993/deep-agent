"""Opt-in expansion of local references in outgoing Chat tool schemas."""

from __future__ import annotations

import json
import re
from typing import Any, Literal
from urllib.parse import unquote

from openai.types.chat import ChatCompletionToolParam
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition

from deep_agent.models.openai_compatible_video import OpenAICompatibleVideoChatModel
from deep_agent.structured_output.schema import MAX_SCHEMA_BYTES, MAX_SCHEMA_DEPTH, MAX_SCHEMA_NODES

INLINE_TOOL_SCHEMA_REFS_TAG = "inline_tool_schema_refs"

_MAPS = {"properties", "patternProperties", "dependentSchemas"}
_LISTS = {"prefixItems", "allOf", "anyOf", "oneOf"}
_CHILDREN = {
    "additionalProperties", "unevaluatedProperties", "propertyNames", "items",
    "additionalItems", "contains", "unevaluatedItems", "not", "if", "then", "else",
    "contentSchema",
}
_ANNOTATIONS = {
    "title", "description", "default", "examples", "deprecated", "readOnly",
    "writeOnly", "$comment",
}
_UNSUPPORTED = {
    "$defs", "definitions", "$id", "id", "$anchor", "$dynamicRef", "$dynamicAnchor",
    "$recursiveRef", "$recursiveAnchor", "$vocabulary", "discriminator", "dependencies",
}
_REFERENCE = re.compile(r"#/\$defs/((?:[^/~]|~[01])+)$")
_Mode = Literal["schema", "map", "list", "data", "opaque"]


class _CannotInline(ValueError):
    pass


def inline_tool_schema_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Expand a bounded, root-local subset, or return the original schema."""
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict) or not definitions:
        return schema

    remaining_bytes = MAX_SCHEMA_BYTES
    remaining_nodes = MAX_SCHEMA_NODES
    used_reference = False

    def spend(size: int) -> None:
        nonlocal remaining_bytes
        remaining_bytes -= size
        if remaining_bytes < 0:
            raise _CannotInline

    def spend_scalar(value: Any) -> None:
        # UTF-8 JSON cannot be shorter than the original string's character count.
        if isinstance(value, str) and len(value) > remaining_bytes:
            raise _CannotInline
        spend(len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")))

    def visit(value: Any, depth: int, active: tuple[str, ...], mode: _Mode) -> Any:
        nonlocal remaining_nodes, used_reference
        remaining_nodes -= 1
        if remaining_nodes < 0 or depth > MAX_SCHEMA_DEPTH:
            raise _CannotInline

        if isinstance(value, dict):
            if len(value) > remaining_nodes:
                raise _CannotInline
            if mode in ("schema", "opaque") and _UNSUPPORTED.intersection(value):
                raise _CannotInline
            if "$ref" in value and mode in ("schema", "opaque"):
                ref = value["$ref"]
                if mode == "opaque" or not isinstance(ref, str) or len(ref) > MAX_SCHEMA_BYTES:
                    raise _CannotInline
                match = _REFERENCE.fullmatch(unquote(ref, errors="strict"))
                annotations = value.keys() - {"$ref"}
                if match is None or not annotations <= _ANNOTATIONS:
                    raise _CannotInline
                key = match[1].replace("~1", "/").replace("~0", "~")
                target = definitions.get(key)
                # Chained aliases consume Python stack even when they do not
                # deepen the expanded JSON tree.
                if len(active) >= MAX_SCHEMA_DEPTH or key in active or not isinstance(target, (dict, bool)):
                    raise _CannotInline
                if isinstance(target, bool) and annotations:
                    raise _CannotInline
                if isinstance(target, dict) and "$schema" in target:
                    raise _CannotInline
                used_reference = True
                if annotations:
                    if len(target) + len(annotations) > remaining_nodes:
                        raise _CannotInline
                    target = {**target, **{k: value[k] for k in annotations}}
                return visit(target, depth, (*active, key), "schema")

            if mode == "schema" and depth > 1 and "$schema" in value:
                raise _CannotInline
            spend(2)
            result = {}
            for index, (key, child) in enumerate(value.items()):
                spend(1 + bool(index))  # colon and any preceding comma
                spend_scalar(key)
                child_mode: _Mode = "data" if mode == "data" else "opaque"
                if mode == "map":
                    child_mode = "schema"
                elif mode == "schema":
                    if key in _MAPS:
                        child_mode = "map"
                    elif key in _LISTS or key == "items" and isinstance(child, list):
                        child_mode = "list"
                    elif key in _CHILDREN:
                        child_mode = "schema"
                    elif key in _ANNOTATIONS or key in ("const", "enum"):
                        child_mode = "data"
                result[key] = visit(child, depth + 1, active, child_mode)
            return result

        if isinstance(value, list):
            spend(2 + max(0, len(value) - 1))
            list_mode: _Mode = "schema" if mode == "list" else "data" if mode == "data" else "opaque"
            return [visit(child, depth + 1, active, list_mode) for child in value]

        spend_scalar(value)
        return value

    try:
        if len(schema) > MAX_SCHEMA_NODES:
            return schema
        result = visit({k: v for k, v in schema.items() if k != "$defs"}, 1, (), "schema")
    except (ValueError, TypeError):
        return schema
    return result if used_reference and isinstance(result, dict) else schema


class InlineToolRefsChatModel(OpenAIChatModel):
    """Keep SDK strict decisions and adapt only the wire parameters."""

    def _map_tool_definition(self, f: ToolDefinition, model_settings: ModelSettings) -> ChatCompletionToolParam:
        tool = super()._map_tool_definition(f, model_settings)
        if f.kind != "output" and tool["type"] == "function":
            function = tool["function"]
            if parameters := function.get("parameters"):
                function["parameters"] = inline_tool_schema_refs(parameters)
        return tool


class InlineToolRefsVideoChatModel(InlineToolRefsChatModel, OpenAICompatibleVideoChatModel):
    """Use the same tool adapter with the existing video content mapper."""
