from __future__ import annotations

from dataclasses import dataclass

from openai.types import chat
from pydantic_ai.models.openai import OpenAIChatModel

DEEPSEEK_OUTPUT_INSTRUCTIONS = (
    "【回复格式要求】面向用户的完整回复必须写在正常的 content 文本字段中。"
    "reasoning/思考内容仅用于内部分析，不要只把最终答案放在 reasoning 里。"
    "即使只是简单问候，也必须在 content 中输出用户可见的文本。"
    "若收到要求返回 text 或调用工具的校验提示，请用 content 字段重新给出正式回复，"
    "不要重复解释校验原因。"
)


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


class DeepSeekChatModel(OpenAIChatModel):
    """OpenAI-chat model with DeepSeek-specific assistant message round-tripping.

    DeepSeek may return user-visible text only in ``reasoning_content``. Pydantic AI
    stores that as ``ThinkingPart``, but the Chat Completions API rejects assistant
    messages with ``content=null`` and no ``tool_calls`` on retry. We mirror reasoning
    text into ``content`` only for that serialization path.
    """

    _MapModelResponseContext = _DeepSeekMapModelResponseContext
