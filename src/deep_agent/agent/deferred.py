from __future__ import annotations

from typing import Any

from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.messages import ToolCallPart

from deep_agent.schemas.chat import DeferredToolResultItem


class DeferredResultConflict(ValueError):
    """Request rejection, not an execution failure or a new conversation fact."""

    code = "deferred_result_conflict"

    def __init__(self) -> None:
        super().__init__("工具确认已处理或已失效，请刷新会话后继续；本次未重复执行。")


async def validate_deferred_results(store, session_id: str, results: DeferredToolResults | None) -> None:
    """Accept results only for the current durable pending response.

    Call after ownership validation and again under the session execution lock.
    Do not search globally by call ID: IDs may recur in later model responses.
    """
    if results is None:
        return
    from deep_agent.db.event_codec import group_message_events, tool_part_references

    rows = await store.list_all_events(session_id)
    pending: dict[str, str] = {}
    for event in group_message_events([
        row.public_dict() if hasattr(row, "public_dict") else row for row in rows
    ]):
        kind = event.get("event_type")
        # A failed resume may occur before consuming any result. Its error event
        # does not invalidate the still-pending response; a durable result does.
        if kind == "user/message":
            pending = {}
        elif kind == "assistant/message":
            pending = {
                ref["call_id"]: ref["tool_name"]
                for ref in tool_part_references(event) if ref["role"] == "call"
            } if event.get("status") == "pending" else {}
        else:
            for ref in tool_part_references(event):
                if ref["role"] == "result" and pending.get(ref["call_id"]) == ref["tool_name"]:
                    pending.pop(ref["call_id"], None)
    submitted = set(results.calls) | set(results.approvals)
    if (not submitted or not submitted <= pending.keys()
            or not set(results.metadata) <= submitted
            or set(results.calls) & set(results.approvals)):
        raise DeferredResultConflict()


def serialize_deferred(requests: DeferredToolRequests) -> dict[str, Any]:
    return {
        "calls": [_tool_call_part_dict(part) for part in requests.calls],
        "approvals": [_tool_call_part_dict(part) for part in requests.approvals],
    }


def build_deferred_tool_results(items: list[DeferredToolResultItem]) -> DeferredToolResults:
    results = DeferredToolResults()
    if len({item.tool_call_id for item in items}) != len(items):
        raise DeferredResultConflict()
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
