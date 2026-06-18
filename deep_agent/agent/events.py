from __future__ import annotations

from functools import singledispatch
from typing import Any

from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    ToolReturnPart,
)


def text_delta(delta: str) -> dict[str, Any]:
    return {"type": "text", "delta": delta}


def tool_call(tool: str, args: Any, tool_call_id: str | None = None) -> dict[str, Any]:
    return {"type": "tool_call", "tool": tool, "args": args, "tool_call_id": tool_call_id}


def tool_result(tool: str, result: Any, tool_call_id: str | None = None) -> dict[str, Any]:
    return {"type": "tool_result", "tool": tool, "result": result, "tool_call_id": tool_call_id}


def final(output: Any, usage: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"type": "final", "output": output, "usage": usage or {}}


def error(message: str) -> dict[str, Any]:
    return {"type": "error", "message": message}


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, (list, dict)):
        return value
    return str(value)


@singledispatch
def map_part(part: Any) -> dict[str, Any] | None:
    return None


@map_part.register(TextPart)
def _map_text_part(part: TextPart) -> dict[str, Any] | None:
    if not part.content:
        return None
    return text_delta(part.content)


@map_part.register(ToolCallPart)
def _map_tool_call_part(part: ToolCallPart) -> dict[str, Any] | None:
    return tool_call(part.tool_name, part.args_as_dict(), part.tool_call_id)


@map_part.register(ToolReturnPart)
def _map_tool_return_part(part: ToolReturnPart) -> dict[str, Any] | None:
    return tool_result(
        part.tool_name,
        _to_jsonable(part.content),
        part.tool_call_id,
    )


@map_part.register(TextPartDelta)
def _map_text_part_delta(part: TextPartDelta) -> dict[str, Any] | None:
    if not part.content_delta:
        return None
    return text_delta(part.content_delta)


@singledispatch
def map_event(event: Any) -> dict[str, Any] | None:
    return None


@map_event.register(PartStartEvent)
@map_event.register(FunctionToolCallEvent)
@map_event.register(FunctionToolResultEvent)
def _map_part_event(
    event: PartStartEvent | FunctionToolCallEvent | FunctionToolResultEvent,
) -> dict[str, Any] | None:
    return map_part(event.part)


@map_event.register(PartDeltaEvent)
def _map_part_delta_event(event: PartDeltaEvent) -> dict[str, Any] | None:
    return map_part(event.delta)
