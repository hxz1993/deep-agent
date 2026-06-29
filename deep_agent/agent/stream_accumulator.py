from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)


@dataclass
class StreamAccumulator:
    """Collect streamed SSE-mapped events safe to persist on client disconnect."""

    _text: str = ""
    _thinking: str = ""
    _tool_calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    _tool_results: list[dict[str, Any]] = field(default_factory=list)

    def ingest(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "text":
            self._text += event.get("delta") or ""
        elif event_type == "thinking":
            self._thinking += event.get("delta") or ""
        elif event_type == "tool_call":
            tool_call_id = event.get("tool_call_id")
            if not tool_call_id:
                return
            entry = self._tool_calls.setdefault(
                tool_call_id,
                {"tool": event.get("tool"), "args": event.get("args")},
            )
            args = event.get("args")
            if args and args != {}:
                entry["args"] = args
        elif event_type == "tool_result":
            self._tool_results.append(event)

    def _complete_tool_calls(self) -> list[ToolCallPart]:
        parts: list[ToolCallPart] = []
        for tool_call_id, entry in self._tool_calls.items():
            args = entry.get("args")
            if not args or args == {}:
                continue
            tool_name = entry.get("tool")
            if not tool_name:
                continue
            parts.append(
                ToolCallPart(
                    tool_name=tool_name,
                    args=args,
                    tool_call_id=tool_call_id,
                )
            )
        return parts

    def has_content(self) -> bool:
        if self._text or self._thinking:
            return True
        if self._complete_tool_calls():
            return True
        return bool(self._tool_results)

    def to_model_messages(self, *, include_tool_calls: bool = True) -> list[ModelMessage]:
        messages: list[ModelMessage] = []
        response_parts: list[Any] = []
        if self._thinking:
            response_parts.append(ThinkingPart(content=self._thinking))
        if self._text:
            response_parts.append(TextPart(content=self._text))
        if include_tool_calls:
            response_parts.extend(self._complete_tool_calls())
        if response_parts:
            messages.append(ModelResponse(parts=response_parts, state="interrupted"))
        if self._tool_results:
            return_parts = [
                ToolReturnPart(
                    tool_name=item["tool"],
                    content=item.get("result"),
                    tool_call_id=item.get("tool_call_id"),
                )
                for item in self._tool_results
                if item.get("tool")
            ]
            if return_parts:
                messages.append(ModelRequest(parts=return_parts))
        return messages
