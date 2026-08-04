from __future__ import annotations

from dataclasses import replace
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelRequestPart, UserPromptPart

from deep_agent.agent.attachments import is_attachment_ref
from deep_agent.agent.context_references import is_context_reference

_message_adapter: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)
_request_part_adapter: TypeAdapter[ModelRequestPart] = TypeAdapter(ModelRequestPart)


def _is_deep_agent_reference(item: Any) -> bool:
    return is_attachment_ref(item) or is_context_reference(item)


def _payload_has_deep_agent_ref(payload: dict[str, Any]) -> bool:
    if payload.get("kind") != "request":
        return False
    for part in payload.get("parts") or []:
        if part.get("part_kind") != "user-prompt":
            continue
        content = part.get("content")
        if not isinstance(content, list):
            continue
        if any(_is_deep_agent_reference(item) for item in content):
            return True
    return False


def _deserialize_request_with_deep_agent_refs(payload: dict[str, Any]) -> ModelRequest:
    parts: list[ModelRequestPart] = []
    for part_payload in payload.get("parts") or []:
        if (
            part_payload.get("part_kind") == "user-prompt"
            and _part_content_has_deep_agent_ref(part_payload.get("content"))
        ):
            parsed = _request_part_adapter.validate_python(
                {**part_payload, "content": ""}
            )
            if not isinstance(parsed, UserPromptPart):
                raise ValueError("Expected a user prompt part")
            parts.append(replace(parsed, content=part_payload["content"]))
            continue
        parts.append(_request_part_adapter.validate_python(part_payload))
    shell = _message_adapter.validate_python({**payload, "parts": []})
    if not isinstance(shell, ModelRequest):
        raise ValueError("Expected a model request")
    return replace(shell, parts=parts)


def _part_content_has_deep_agent_ref(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    return any(_is_deep_agent_reference(item) for item in content)


def _request_has_deep_agent_ref(message: ModelRequest) -> bool:
    for part in message.parts:
        if isinstance(part, UserPromptPart) and _part_content_has_deep_agent_ref(
            part.content
        ):
            return True
    return False


def _serialize_user_prompt_part_with_refs(part: UserPromptPart) -> dict[str, Any]:
    payload = _request_part_adapter.dump_python(
        replace(part, content=""),
        mode="json",
    )
    payload["content"] = part.content
    return payload


def _serialize_request_with_deep_agent_refs(message: ModelRequest) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, UserPromptPart) and _part_content_has_deep_agent_ref(
            part.content
        ):
            parts.append(_serialize_user_prompt_part_with_refs(part))
        else:
            parts.append(_request_part_adapter.dump_python(part, mode="json"))
    payload = _message_adapter.dump_python(replace(message, parts=[]), mode="json")
    payload["parts"] = parts
    return payload


def serialize_message(message: ModelMessage) -> dict:
    """JSON-compatible dict for DB persistence."""
    if isinstance(message, ModelRequest) and _request_has_deep_agent_ref(message):
        return _serialize_request_with_deep_agent_refs(message)
    return _message_adapter.dump_python(message, mode="json")


def deserialize_message(payload: dict) -> ModelMessage:
    if _payload_has_deep_agent_ref(payload):
        return _deserialize_request_with_deep_agent_refs(payload)
    return _message_adapter.validate_python(payload)
