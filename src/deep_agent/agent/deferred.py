from __future__ import annotations

from typing import Any

from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.messages import ToolCallPart

from deep_agent.schemas.chat import DeferredToolResultItem


def serialize_deferred(requests: DeferredToolRequests) -> dict[str, Any]:
    return {
        "calls": [_tool_call_part_dict(part) for part in requests.calls],
        "approvals": [_tool_call_part_dict(part) for part in requests.approvals],
    }


def build_deferred_tool_results(items: list[DeferredToolResultItem]) -> DeferredToolResults:
    results = DeferredToolResults()
    for item in items:
        if item.approved is not None:
            if item.denied_message:
                results.approvals[item.tool_call_id] = ToolDenied(message=item.denied_message)
            else:
                results.approvals[item.tool_call_id] = item.approved
        else:
            results.calls[item.tool_call_id] = item.result
    return results


def _tool_call_part_dict(part: ToolCallPart) -> dict[str, Any]:
    return {
        "tool": part.tool_name,
        "args": part.args_as_dict(),
        "tool_call_id": part.tool_call_id,
    }
