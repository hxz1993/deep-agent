"""Bounded correction instructions without copying candidate business values."""
from __future__ import annotations

import json
from typing import Any

MAX_CORRECTION_CHARS = 6000


class OutputArgumentsError(ValueError):
    def __init__(self, constraint: str, message: str, *, path: str = "/"):
        self.error = {"code": "invalid_json", "path": path[:160],
                      "constraint": constraint, "message": message[:256]}
        super().__init__(message)


def _property_names(schema: dict[str, Any]) -> set[str]:
    """Only echo keys declared by the trusted configuration, not dynamic data."""
    names = {"response"}
    pending: list[Any] = [schema]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                names.update(properties)
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return names


def decode_output_arguments(args: Any, schema: dict[str, Any]) -> Any:
    """Keep strict JSON and transparent root-wrapper semantics unchanged."""
    if isinstance(args, str):
        declared = _property_names(schema)

        def reject_constant(_value):
            raise OutputArgumentsError("json", "JSON numbers must be finite; NaN and Infinity are not permitted.")

        def unique_object(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    label = (" " + json.dumps(key, ensure_ascii=False)) if key in declared else ""
                    raise OutputArgumentsError("duplicateKey", f"Duplicate JSON property{label}; each property must occur only once.")
                result[key] = item
            return result

        try:
            args = json.loads(args, parse_constant=reject_constant, object_pairs_hook=unique_object)
        except OutputArgumentsError:
            raise
        except json.JSONDecodeError as exc:
            # The decoder position is safe; its document and snippets are not.
            raise OutputArgumentsError("json", f"Invalid JSON syntax at line {exc.lineno}, column {exc.colno}. "
                                       "Check separators, quotes and closing brackets; resubmit complete JSON.") from exc
        except (ValueError, RecursionError):
            raise OutputArgumentsError("json", "JSON nesting or numeric size exceeds parsing limits; resubmit valid finite JSON.") from None
    if schema.get("type") == "object":
        return args
    if not isinstance(args, dict) or "response" not in args:
        raise OutputArgumentsError("wrapper", "This output uses a non-object root. Put the complete value in the response argument.",
                                   path="/response")
    return args["response"]


def protocol_error(reason: str, output_name: str) -> dict[str, str]:
    messages = {
        "missing_output": f"No final output call was returned. Call {output_name} with one complete result.",
        "multiple_output_calls": f"More than one final output call was returned. Call {output_name} only once per response.",
        "mixed_output_calls": "Final output and other tool calls were mixed. The host did not dispatch the rejected calls; separate them.",
    }
    return {"code": reason, "path": "/", "constraint": "outputCall",
            "message": messages.get(reason, "The response does not match the configured output protocol.")[:256]}


def correction_feedback(reason: str, errors: list[dict[str, str]], output_name: str) -> str:
    instructions = {
        "missing_output": f"Finish by calling the configured output tool {output_name} with one complete JSON result matching its schema. "
                          "Do not end with only thinking or plain text.",
        "multiple_output_calls": f"Call {output_name} exactly once per response. Submit one complete JSON result matching its schema.",
        "mixed_output_calls": f"Use separate responses: first call any necessary ordinary tools without {output_name}; "
                              f"after receiving their results, call only {output_name} with the complete JSON result.",
        "invalid_json": f"Fix the JSON errors below and call {output_name} again with complete valid JSON arguments. "
                        "Resubmit the entire configured result, not a partial correction to the previous arguments. Preserve verified facts.",
        "schema_validation_failed": f"Correct the listed paths to match the configured schema, then call {output_name} "
                                    "with one complete JSON result. Preserve verified facts and required fields.",
    }
    payload: dict[str, Any] = {"code": reason, "errors": [],
                               "instruction": instructions.get(reason, f"Call {output_name} with a complete result matching its schema.")}

    def encode():
        return json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))

    for index, error in enumerate(errors):
        payload["errors"].append(error)
        # Reserve the actual truncation marker before measuring, rather than
        # slicing the serialized JSON and potentially losing its closing brace.
        remaining = len(errors) - index - 1
        if remaining:
            payload["truncated_errors"] = remaining
        else:
            payload.pop("truncated_errors", None)
        if len(encode()) > MAX_CORRECTION_CHARS:
            payload["errors"].pop()
            payload["truncated_errors"] = len(errors) - index
            break
    return encode()
