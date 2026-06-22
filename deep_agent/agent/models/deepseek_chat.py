from __future__ import annotations

from dataclasses import dataclass, replace

from openai.types import chat
from pydantic_ai.messages import (
    BaseToolCallPart,
    ModelResponse,
    ModelResponsePart,
    TextPart,
    ThinkingPart,
)
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIStreamedResponse


def _thinking_fallback_content(thinkings: dict[str, list[str]]) -> str | None:
    if not thinkings:
        return None
    parts: list[str] = []
    for contents in thinkings.values():
        parts.extend(content for content in contents if content)
    if not parts:
        return None
    return "\n\n".join(parts)


def _promote_thinking_only_parts(parts: list[ModelResponsePart]) -> list[ModelResponsePart]:
    """Turn thinking-only model output into text so pydantic_ai accepts it as final output.

    DeepSeek may place user-visible replies in ``reasoning_content``, which pydantic_ai
    stores as ``ThinkingPart``. Without text or tool calls the agent loop retries and
    persists multiple assistant messages.
    """
    if not parts:
        return parts
    if any(isinstance(part, TextPart) and part.content for part in parts):
        return parts
    if any(isinstance(part, BaseToolCallPart) for part in parts):
        return parts
    if not all(isinstance(part, ThinkingPart) for part in parts):
        return parts

    thinking_text = _thinking_fallback_content(
        {str(index): [part.content] for index, part in enumerate(parts) if part.content}
    )
    if thinking_text is None:
        return parts
    return [TextPart(content=thinking_text)]


@dataclass
class _DeepSeekMapModelResponseContext(OpenAIChatModel._MapModelResponseContext):
    """Map assistant history for DeepSeek while satisfying Chat Completions validation."""

    def _into_message_param(self) -> chat.ChatCompletionAssistantMessageParam | None:
        message_param = super()._into_message_param()
        if message_param is None:
            return None
        if message_param.get("tool_calls"):
            return message_param
        if message_param.get("content"):
            return message_param

        fallback = _thinking_fallback_content(self.thinkings)
        if fallback is not None:
            message_param["content"] = fallback
        return message_param


@dataclass
class DeepSeekStreamedResponse(OpenAIStreamedResponse):
    """Streamed DeepSeek response that promotes thinking-only output at stream end."""

    def get(self) -> ModelResponse:
        response = super().get()
        promoted = _promote_thinking_only_parts(list(response.parts))
        if promoted == list(response.parts):
            return response
        return replace(response, parts=promoted)


class DeepSeekChatModel(OpenAIChatModel):
    """OpenAI-chat model with DeepSeek-specific assistant message round-tripping.

    DeepSeek may return user-visible text only in ``reasoning_content``. Pydantic AI
    stores that as ``ThinkingPart``, but the Chat Completions API rejects assistant
    messages with ``content=null`` and no ``tool_calls`` on retry. We mirror reasoning
    text into ``content`` only for that serialization path, and promote thinking-only
    responses to ``TextPart`` so the agent loop does not retry.
    """

    _MapModelResponseContext = _DeepSeekMapModelResponseContext

    def _process_response(self, response: chat.ChatCompletion | str) -> ModelResponse:
        model_response = super()._process_response(response)
        promoted = _promote_thinking_only_parts(list(model_response.parts))
        if promoted == list(model_response.parts):
            return model_response
        return replace(model_response, parts=promoted)

    @property
    def _streamed_response_cls(self) -> type[OpenAIStreamedResponse]:
        return DeepSeekStreamedResponse
