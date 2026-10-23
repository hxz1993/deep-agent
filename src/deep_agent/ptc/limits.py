"""Program limits are independent of model-visible tool-result pagination."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel
from pydantic_core import to_jsonable_python
from pydantic_ai import ToolFailed
from pydantic_ai.messages import ToolReturn

from deep_agent.ptc.diagnostics import current_collector

PTC_MAX_MEMORY_BYTES = 256 * 1024 * 1024
PTC_MAX_RESULT_BYTES = 32 * 1024 * 1024
PTC_EXECUTION_SECONDS = 120
PTC_WALL_TIMEOUT_SECONDS = 120
PTC_MAX_TOOL_CALLS = 100
PTC_BUILTIN_METADATA_KEY = "deepagent_ptc_builtin"


def nested_collector(ctx: Any, call: Any) -> Any:
    """Recognize a child registered by this SDK execution, never by an ID prefix."""
    collector = current_collector.get()
    if collector is None or collector.execution is None:
        return None
    if collector.run_id != ctx.deps.run_id or collector.session_id != ctx.deps.session_id:
        return None
    registered = collector.execution.nested_calls.get(call.tool_call_id)
    if registered is None or registered.tool_name != call.tool_name:
        return None
    return collector


def check_result_size(result: Any) -> None:
    """Count compact UTF-8 JSON, retaining Monty's native binary semantics.

    Walk containers without copying them; encode strings in bounded chunks.
    Metadata/content aren't passed to Monty and must not count as its value.
    Non-JSON leaves use the same Pydantic conversion as the installed SDK.
    """
    value = result.return_value if isinstance(result, ToolReturn) else result
    size = 0
    active: set[int] = set()

    def charge(count: int) -> None:
        nonlocal size
        size += count
        if size > PTC_MAX_RESULT_BYTES:
            raise ToolFailed(
                f"PTC internal result exceeds the {PTC_MAX_RESULT_BYTES} byte limit; "
                "reduce the query or aggregate at the source. No truncated result was returned."
            )

    def string_size(text: str) -> None:
        charge(2)
        for offset in range(0, len(text), 4096):
            encoded = json.dumps(text[offset:offset + 4096], ensure_ascii=False)[1:-1]
            charge(len(encoded.encode("utf-8")))

    def walk(item: Any) -> None:
        if item is None:
            charge(4)
        elif item is Ellipsis:
            charge(3)
        elif isinstance(item, bool):
            charge(4 if item else 5)
        elif isinstance(item, str):
            string_size(item)
        elif isinstance(item, (bytes, bytearray)):
            charge(len(item))
        elif isinstance(item, (int, float)):
            charge(len(json.dumps(item)))
        elif isinstance(item, (Mapping, list, tuple, set, frozenset)):
            identity = id(item)
            if identity in active:
                raise ToolFailed("PTC internal result contains a circular value")
            active.add(identity)
            try:
                charge(2)
                first = True
                values = item.items() if isinstance(item, Mapping) else item
                for child in values:
                    if not first:
                        charge(1)
                    first = False
                    if isinstance(item, Mapping):
                        key, child = child
                        if not isinstance(key, str):
                            key = next(iter(to_jsonable_python({key: None})))
                        string_size(key)
                        charge(1)
                    walk(child)
            finally:
                active.remove(identity)
        elif isinstance(item, BaseModel):
            walk(item.model_dump(mode="python"))
        else:
            walk(to_jsonable_python(item))

    try:
        walk(value)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ToolFailed("PTC internal result cannot be represented safely") from exc
