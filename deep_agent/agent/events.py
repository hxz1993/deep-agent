from __future__ import annotations

from typing import Any


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
