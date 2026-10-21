"""JSON Schema admission, with no I/O, instance mutation or implicit defaults."""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from typing import Any
from urllib.parse import unquote

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

MAX_SCHEMA_BYTES = 64 * 1024
MAX_SCHEMA_DEPTH = 16
MAX_SCHEMA_NODES = 2048
MAX_SCHEMA_PROPERTIES = 512
MAX_SCHEMA_ENUM_ITEMS = 1024
MAX_PATTERN_LENGTH = 512
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_ERRORS = 20
MAX_ERROR_CHARS = 256
CACHE_SIZE = 128
SUPPORTED_FORMATS = frozenset({"date", "date-time", "time", "uuid"})
SUPPORTED_DIALECTS = frozenset({"https://json-schema.org/draft/2020-12/schema", "https://json-schema.org/draft/2020-12/schema#"})
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z", re.ASCII)
_MAP_SCHEMAS = frozenset({"properties", "patternProperties", "$defs", "dependentSchemas"})
_SINGLE_SCHEMAS = frozenset({"additionalProperties", "unevaluatedProperties", "propertyNames", "items", "contains", "unevaluatedItems", "not", "if", "then", "else", "contentSchema"})
_LIST_SCHEMAS = frozenset({"prefixItems", "allOf", "anyOf", "oneOf"})


def validate_output_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("output name must contain 1–64 ASCII letters, digits or underscores and start with a letter")
    return name


def json_bytes(value: Any) -> bytes:
    """Strict JSON, rejecting Python-only values, non-string keys and NaN."""
    pending = [(value, False)]
    containers: set[int] = set()
    while pending:
        current, leaving = pending.pop()
        if leaving:
            containers.remove(id(current))
            continue
        kind = type(current)
        if kind in (dict, list):
            marker = id(current)
            if marker in containers:
                raise ValueError("value must be a JSON tree")
            containers.add(marker)
            pending.append((current, True))
            if kind is dict:
                if any(type(key) is not str for key in current):
                    raise ValueError("JSON object keys must be strings")
                pending.extend((child, False) for child in current.values())
            else:
                pending.extend((child, False) for child in current)
        elif kind not in (str, int, float, bool, type(None)):
            raise ValueError("value contains a non-JSON type")
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise ValueError("value is not finite, UTF-8 JSON") from exc


def _tree_limits(value: Any) -> None:
    pending = [(value, 1)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if depth > MAX_SCHEMA_DEPTH:
            raise ValueError(f"Schema exceeds depth {MAX_SCHEMA_DEPTH}")
        if count > MAX_SCHEMA_NODES:
            raise ValueError(f"Schema exceeds {MAX_SCHEMA_NODES} nodes")
        if isinstance(node, dict):
            pending.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            pending.extend((child, depth + 1) for child in node)


def _children(schema: dict[str, Any]):
    for key in _MAP_SCHEMAS:
        value = schema.get(key)
        if isinstance(value, dict):
            for name, child in value.items():
                yield key, name, child
    for key in _SINGLE_SCHEMAS:
        if key in schema:
            yield key, None, schema[key]
    for key in _LIST_SCHEMAS:
        value = schema.get(key)
        if isinstance(value, list):
            for index, child in enumerate(value):
                yield key, index, child


def _check_keywords(root: dict[str, Any]) -> None:
    pending = [(root, True)]
    properties = enums = 0
    while pending:
        schema, is_root = pending.pop()
        if isinstance(schema, bool):
            continue
        if not isinstance(schema, dict):
            raise ValueError("Schema children must be JSON Schema objects or booleans")
        if "$schema" in schema and schema["$schema"] not in SUPPORTED_DIALECTS:
            raise ValueError("Only JSON Schema Draft 2020-12 is supported")
        if any(key in schema for key in ("$dynamicRef", "$dynamicAnchor", "$recursiveRef", "$recursiveAnchor")):
            raise ValueError("Dynamic and recursive Schema references are not supported")
        # Local refs are resolved against this one document. Nested resources
        # would change that scope and cannot be interpreted as root-local refs.
        if not is_root and "$id" in schema:
            raise ValueError("Nested Schema resources ($id) are not supported")
        if "format" in schema and schema["format"] not in SUPPORTED_FORMATS:
            raise ValueError("Supported formats are date, date-time, time and uuid")
        if "pattern" in schema:
            pattern = schema["pattern"]
            if not isinstance(pattern, str) or len(pattern) > MAX_PATTERN_LENGTH:
                raise ValueError(f"Schema pattern exceeds {MAX_PATTERN_LENGTH} characters")
            try:
                re.compile(pattern)
            except (re.error, OverflowError, RecursionError) as exc:
                raise ValueError("Invalid Schema regular expression") from exc
        patterns = schema.get("patternProperties", {})
        if isinstance(patterns, dict):
            for pattern in patterns:
                if len(pattern) > MAX_PATTERN_LENGTH:
                    raise ValueError(f"Schema pattern exceeds {MAX_PATTERN_LENGTH} characters")
                try:
                    re.compile(pattern)
                except (re.error, OverflowError, RecursionError) as exc:
                    raise ValueError("Invalid Schema regular expression") from exc
        properties += len(schema.get("properties", {})) if isinstance(schema.get("properties"), dict) else 0
        enums += len(schema.get("enum", [])) if isinstance(schema.get("enum"), list) else 0
        if properties > MAX_SCHEMA_PROPERTIES:
            raise ValueError(f"Schema exceeds {MAX_SCHEMA_PROPERTIES} properties")
        if enums > MAX_SCHEMA_ENUM_ITEMS:
            raise ValueError(f"Schema exceeds {MAX_SCHEMA_ENUM_ITEMS} enum entries")
        pending.extend((child, False) for _, _, child in _children(schema))


def _resolve(root: dict[str, Any], ref: Any) -> Any:
    if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
        raise ValueError("Only local #/$defs/ Schema references are supported")
    fragment = unquote(ref[2:])
    node: Any = root
    for raw in fragment.split("/"):
        if re.search(r"~(?![01])", raw):
            raise ValueError("Invalid JSON pointer in Schema reference")
        key = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or key not in node:
            raise ValueError("Schema reference target does not exist")
        node = node[key]
    if not isinstance(node, (dict, bool)):
        raise ValueError("Schema reference must target a Schema")
    return node


def _expand(root: dict[str, Any]) -> dict[str, Any]:
    visited = 0

    def visit(schema: Any, refs: tuple[str, ...] = (), depth: int = 1) -> Any:
        nonlocal visited
        visited += 1
        if visited > MAX_SCHEMA_NODES or depth > MAX_SCHEMA_DEPTH:
            raise ValueError("Expanded Schema exceeds node or depth limit")
        if isinstance(schema, bool):
            return schema
        result = dict(schema)
        # Expand every definition, even unused ones, to detect hidden cycles.
        if "$defs" in result:
            for child in result["$defs"].values():
                visit(child, refs, depth + 1)
            result.pop("$defs")
        if "$ref" in result:
            ref = result.pop("$ref")
            if ref in refs:
                raise ValueError("Cyclic Schema references are not supported")
            target = visit(_resolve(root, ref), (*refs, ref), depth)
            if not result:
                return target
            # Draft 2020-12 ref siblings are constraints too, not overrides.
            result = {"allOf": [target, result]}
        for key, name, child in list(_children(result)):
            expanded = visit(child, refs, depth + 1)
            if name is None:
                result[key] = expanded
            else:
                result[key] = dict(result[key]) if isinstance(result[key], dict) else list(result[key])
                result[key][name] = expanded
        return result

    return visit(root)


@lru_cache(maxsize=CACHE_SIZE)
def _normalize_cached(encoded: bytes) -> tuple[bytes, bytes]:
    schema = json.loads(encoded)
    _tree_limits(schema)
    _check_keywords(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except (SchemaError, RecursionError, TypeError, ValueError) as exc:
        # jsonschema's normal message includes instance values; do not expose it.
        path = "/".join(str(value) for value in getattr(exc, "absolute_path", ()))[:160]
        raise ValueError(f"Invalid JSON Schema at /{path}") from exc
    expanded = _expand(schema)
    _tree_limits(expanded)
    _check_keywords(expanded)
    expanded_bytes = json_bytes(expanded)
    if len(expanded_bytes) > MAX_SCHEMA_BYTES:
        raise ValueError(f"Expanded Schema exceeds {MAX_SCHEMA_BYTES} UTF-8 bytes")
    return encoded, expanded_bytes


def normalize_schema(schema: Any) -> dict[str, Any]:
    if type(schema) is not dict:
        raise ValueError("Schema must be a JSON Schema object")
    encoded = json_bytes(schema)
    if len(encoded) > MAX_SCHEMA_BYTES:
        raise ValueError(f"Schema exceeds {MAX_SCHEMA_BYTES} UTF-8 bytes")
    return json.loads(_normalize_cached(encoded)[0])


def expand_schema(schema: Any) -> dict[str, Any]:
    normalized = normalize_schema(schema)
    return json.loads(_normalize_cached(json_bytes(normalized))[1])


def schema_digest(schema: Any) -> str:
    return hashlib.sha256(json_bytes(normalize_schema(schema))).hexdigest()


def normalize_output_config(config: Any) -> dict[str, Any]:
    if hasattr(config, "model_dump"):
        config = config.model_dump()
    if not isinstance(config, dict):
        raise ValueError("output_config must be an object")
    name = validate_output_name(config.get("name"))
    description = config.get("description")
    if description is not None and (not isinstance(description, str) or len(description) > 8192):
        raise ValueError("output description must contain at most 8192 characters")
    return {"name": name, "description": description, "json_schema": normalize_schema(config.get("json_schema"))}
