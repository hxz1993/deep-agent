from __future__ import annotations

from dataclasses import dataclass

from openai.types import chat
from pydantic_ai.models.openai import OpenAIChatModel

def _thinking_fallback_content(thinkings: dict[str, list[str]]) -> str | None:
    if not thinkings:
        return None
    parts: list[str] = []
    for contents in thinkings.values():
        parts.extend(content for content in contents if content)
    if not parts:
        return None
    return "\n\n".join(parts)


@dataclass
class _DeepSeekMapModelResponseContext(OpenAIChatModel._MapModelResponseContext):
    """Map assistant history for DeepSeek while satisfying Chat Completions validation."""

    def _into_message_param(self) -> chat.ChatCompletionAssistantMessageParam | None:
        fallback = _thinking_fallback_content(self.thinkings)
        if fallback is not None and not self.texts and not self.tool_calls:
            self.texts.append(fallback)

        message_param = super()._into_message_param()
        if message_param is None:
            return None
        if message_param.get("tool_calls"):
            return message_param
        if message_param.get("content"):
            return message_param

        if fallback is not None:
            message_param["content"] = fallback
        return message_param


class DeepSeekChatModel(OpenAIChatModel):
    """OpenAI-chat model with DeepSeek-specific assistant message round-tripping.

    DeepSeek may return user-visible text only in ``reasoning_content``. Pydantic AI
    stores that as ``ThinkingPart``, but the Chat Completions API rejects assistant
    messages with ``content=null`` and no ``tool_calls`` on retry. We mirror reasoning
    text into ``content`` only for that serialization path.
    """

    _MapModelResponseContext = _DeepSeekMapModelResponseContext
