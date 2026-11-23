"""One-shot validator subprocess. No business payload is written to logs/files."""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry
from referencing.exceptions import NoSuchResource


def _deny_retrieval(uri: str):
    raise NoSuchResource(ref=uri)


_DATE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[Tt](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?([Zz]|[+-]\d{2}:\d{2})\Z", re.ASCII)
_TIME = re.compile(r"(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?([Zz]|[+-]\d{2}:\d{2})\Z", re.ASCII)
_FORMATS = FormatChecker(formats=[])


def _rfc3339(value: str, *, date_time: bool) -> bool:
    match = (_DATE_TIME if date_time else _TIME).fullmatch(value)
    if not match:
        return False
    hour, minute, second = map(int, match.groups()[:3])
    if hour > 23 or minute > 59 or second > 60:
        return False
    zone = match.group(4)
    if len(zone) > 1 and (int(zone[1:3]) > 23 or int(zone[4:6]) > 59):
        return False
    if date_time:
        date.fromisoformat(value[:10])
    return True


@_FORMATS.checks("date", raises=ValueError)
def _date(value):
    return not isinstance(value, str) or (bool(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value)) and bool(date.fromisoformat(value)))


@_FORMATS.checks("date-time", raises=ValueError)
def _date_time(value):
    return not isinstance(value, str) or _rfc3339(value, date_time=True)


@_FORMATS.checks("time", raises=ValueError)
def _time(value):
    return not isinstance(value, str) or _rfc3339(value, date_time=False)


@_FORMATS.checks("uuid", raises=ValueError)
def _uuid(value):
    return not isinstance(value, str) or (bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value)) and bool(UUID(value)))


_MAX_ERRORS = 20
_MAX_CONTEXT_NODES = 128
_MAX_BRANCHES = 3
_MAX_PATH_SCHEMA_NODES = 2048
_FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)


def _pointer(path) -> str:
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in path)


def _path_schemas(schemas, budget):
    """Find static declarations at this instance location, without revalidation."""
    pending, seen = list(schemas), set()
    while pending and budget[0] > 0:
        schema = pending.pop()
        if not isinstance(schema, dict) or id(schema) in seen:
            continue
        seen.add(id(schema))
        budget[0] -= 1
        yield schema
        for keyword in ("allOf", "anyOf", "oneOf"):
            pending.extend(schema.get(keyword, []))
        for keyword in ("if", "then", "else"):
            if keyword in schema:
                pending.append(schema[keyword])
        pending.extend(schema.get("dependentSchemas", {}).values())


def _safe_instance_path(path, schema):
    """Keep Schema-owned fields/indexes; dynamic map keys may contain secrets."""
    states, safe, budget = [schema], [], [_MAX_PATH_SCHEMA_NODES]
    for part in path:
        variants = list(_path_schemas(states, budget))
        children = []
        if isinstance(part, str):
            declared = False
            for variant in variants:
                properties = variant.get("properties", {})
                if part in properties:
                    declared = True
                    children.append(properties[part])
                # Do not rerun patternProperties regex just to format a path.
                # Every possible value Schema may declare later static fields.
                children.extend(variant.get("patternProperties", {}).values())
                for keyword in ("additionalProperties", "unevaluatedProperties"):
                    if keyword in variant:
                        children.append(variant[keyword])
            safe.append(part if declared else "<key>")
        else:
            safe.append(part)
            for variant in variants:
                prefix = variant.get("prefixItems", [])
                if 0 <= part < len(prefix):
                    children.append(prefix[part])
                elif "items" in variant:
                    children.append(variant["items"])
                for keyword in ("contains", "unevaluatedItems"):
                    if keyword in variant:
                        children.append(variant[keyword])
        states = children
    return tuple(safe)


def _json_type(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    return "number"


def _schema_label(value, limit: int = 100) -> str:
    """Only Schema-owned scalars are suitable for diagnostic expectations."""
    if isinstance(value, (dict, list)):
        return "configured " + _json_type(value)
    text = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _details(error):
    """Yield paths and explanations, never jsonschema's candidate-bearing message."""
    path = tuple(error.absolute_path)
    rule, expected, value = error.validator, error.validator_value, error.instance
    schema = error.schema if isinstance(error.schema, dict) else {}
    if rule == "required" and isinstance(value, dict):
        for key in expected:
            if key not in value:
                yield (*path, key), "Required property " + _schema_label(key, 80) + " is missing."
        return
    if rule == "dependentRequired" and isinstance(value, dict):
        for key, dependencies in expected.items():
            if key in value:
                for dependency in dependencies:
                    if dependency not in value:
                        yield (*path, dependency), "Property " + _schema_label(key, 64) + " requires " + _schema_label(dependency, 64) + "."
        return
    if rule == "additionalProperties" and expected is False and isinstance(value, dict):
        properties, patterns = schema.get("properties", {}), schema.get("patternProperties", {})
        if patterns:
            # Validation already evaluated these expressions. Re-running them
            # only for diagnostics could spend another unbounded regex budget.
            yield path, "Properties must be declared in properties or match patternProperties."
            return
        for key in value:
            if key in properties:
                continue
            # Unknown keys can themselves hold emails, tokens or whole sentences.
            # Expose conventional field identifiers, not arbitrary candidate strings.
            if _FIELD_NAME.fullmatch(key):
                yield (*path, key), "Unexpected property " + _schema_label(key, 80) + "; remove it."
            else:
                yield path, "An unexpected property is present; use only the configured properties."
        return
    if rule == "type":
        names = expected if isinstance(expected, list) else [expected]
        message = "Expected type " + " or ".join(names) + "; actual type=" + _json_type(value) + "."
    elif rule in {"minItems", "maxItems", "minLength", "maxLength", "minProperties", "maxProperties"}:
        category = "items" if rule.endswith("Items") else "characters" if rule.endswith("Length") else "properties"
        limit = "minimum" if rule.startswith("min") else "maximum"
        message = f"Expected {limit}={expected} {category}; actual={len(value)}."
    elif rule in {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}:
        message = f"Number must satisfy {rule}=" + _schema_label(expected) + "."
    elif rule == "enum":
        choices = [_schema_label(choice, 30) for choice in expected[:6]]
        message = "Expected one configured value: " + ", ".join(choices)
        if len(expected) > 6:
            message += f" (showing 6 of {len(expected)})."
    elif rule == "const":
        message = "Expected constant " + _schema_label(expected) + "."
    elif rule == "pattern":
        message = "String must match pattern " + _schema_label(expected, 120) + "."
    elif rule == "format":
        message = "String must have format " + _schema_label(expected, 80) + "."
    elif rule == "uniqueItems":
        message = "Array items must be unique."
    elif rule == "oneOf":
        message = ("Must match exactly one branch; no branch matched." if error.context
                   else "Must match exactly one branch; multiple branches matched.")
    elif rule == "anyOf":
        message = "Must match at least one branch; no branch matched."
    elif rule == "not":
        message = "Value matches a prohibited Schema; use a permitted alternative."
    elif rule in {"contains", "minContains", "maxContains"}:
        minimum = schema.get("minContains", 1)
        maximum = schema.get("maxContains")
        message = f"Array must contain at least {minimum} matching items"
        message += f" and at most {maximum}." if maximum is not None else "."
    elif rule in {"unevaluatedProperties", "unevaluatedItems"}:
        message = "Value contains properties or items not permitted by the selected Schema."
    elif error.schema is False:
        message = "Value is not allowed by this Schema."
    else:
        message = "Value does not satisfy the configured " + str(rule or "schema") + " constraint."
    yield path, message


def _matching_branches(error):
    """Prefer candidate-type-compatible branches, without changing validation truth."""
    grouped = {}
    for child in error.context[:_MAX_CONTEXT_NODES]:
        relative = list(child.schema_path)
        if relative and isinstance(relative[0], int):
            grouped.setdefault(relative[0], []).append(child)
    branches = error.validator_value
    compatible = []
    for index in grouped:
        branch = branches[index]
        types = branch.get("type") if isinstance(branch, dict) else None
        if types:
            matches = any(Draft202012Validator.TYPE_CHECKER.is_type(error.instance, kind)
                          for kind in (types if isinstance(types, list) else [types]))
        else:
            # A branch can express its root type through allOf instead of its
            # immediate `type`. Such a mismatch is still less relevant than a
            # branch whose object/array shape already matches the candidate.
            matches = not any(child.validator == "type"
                              and tuple(child.absolute_path) == tuple(error.absolute_path)
                              for child in grouped[index])
        if matches:
            compatible.append(index)
    # If several object alternatives remain, their branch labels keep the leaves
    # explicitly conditional. Report all small sets, or the three nearest choices.
    selected = compatible or list(grouped)
    selected = sorted(selected, key=lambda index: (len(grouped[index]), index))[:_MAX_BRANCHES]
    return [(index, grouped[index]) for index in sorted(selected)]


def validate(payload: dict) -> list[dict]:
    validator = Draft202012Validator(payload["schema"], format_checker=_FORMATS, registry=Registry(retrieve=_deny_retrieval))
    errors, seen = [], set()
    visited = 0
    for error in validator.iter_errors(payload["value"]):
        pending = [(error, "")]
        while pending and visited < _MAX_CONTEXT_NODES:
            current, branch = pending.pop()
            visited += 1
            constraint = str(current.validator or "schema")[:64]
            base_path = tuple(current.absolute_path)
            safe_path = _safe_instance_path(base_path, payload["schema"])
            for path, detail in _details(current):
                # Missing Schema fields and approved ordinary unknown-field
                # names are generated by _details, not existing map path keys.
                pointer = _pointer((*safe_path, *path[len(base_path):]))[:160]
                item = {"path": pointer, "constraint": constraint,
                        "message": (branch + detail)[:256]}
                key = (item["path"], item["constraint"], item["message"])
                if key in seen:
                    continue
                seen.add(key)
                errors.append(item)
                if len(errors) >= _MAX_ERRORS:
                    return errors
            if current.validator in {"anyOf", "oneOf"} and current.context:
                children = [(child, f"{current.validator} branch {index + 1}: ")
                            for index, group in _matching_branches(current) for child in group]
                pending.extend(reversed(children[:_MAX_CONTEXT_NODES - visited]))
        if visited >= _MAX_CONTEXT_NODES:
            break
    return errors


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(64 * 1024 + 1024 * 1024 + 256)
        payload = json.loads(raw)
        errors = validate(payload)
        sys.stdout.write(json.dumps({"errors": errors}, ensure_ascii=True, separators=(",", ":")))
        return 0
    except Exception:
        # A protocol/validator fault is not a candidate validation error.
        sys.stdout.write('{"error":"worker_failed"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
