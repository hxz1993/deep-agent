from __future__ import annotations

from dataclasses import dataclass, replace

from loguru import logger
from pydantic_ai.capabilities.abstract import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, RetryPromptPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import RunContext

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.history import is_retry_only_request, is_thinking_only_response
from deep_agent.agent.models.deepseek_chat import DeepSeekChatModel

DEEPSEEK_THINKING_RETRY_PROMPT = (
    "上一条回复只写在 reasoning/思考字段中，用户看不到。"
    "请直接在 content 文本字段中重新输出完整回复，不要输出 reasoning 或思考内容。"
)


def _thinking_preview(response: ModelResponse) -> str:
    chunks: list[str] = []
    for part in response.parts:
        if getattr(part, "content", None):
            chunks.append(str(part.content))
    preview = " ".join(chunks).strip()
    if len(preview) > 120:
        return preview[:117] + "..."
    return preview


def rewrite_messages_for_thinking_retry(messages: list[ModelMessage]) -> tuple[list[ModelMessage], bool]:
    """Rewrite the latest output-validation retry when it follows a thinking-only turn."""
    if len(messages) < 2:
        return messages, False

    last = messages[-1]
    previous = messages[-2]
    if not isinstance(last, ModelRequest) or not is_retry_only_request(last):
        return messages, False
    if not isinstance(previous, ModelResponse) or not is_thinking_only_response(previous):
        return messages, False

    retry_parts: list[RetryPromptPart] = []
    changed = False
    for part in last.parts:
        if isinstance(part, RetryPromptPart) and part.tool_name is None:
            retry_parts.append(replace(part, content=DEEPSEEK_THINKING_RETRY_PROMPT))
            changed = True
        else:
            retry_parts.append(part)

    if not changed:
        return messages, False

    return [*messages[:-1], replace(last, parts=retry_parts)], True


@dataclass
class DeepSeekThinkingRetry(AbstractCapability[AgentDeps]):
    """On thinking-only output retries, steer DeepSeek to answer in content without reasoning."""

    async def before_model_request(
        self,
        ctx: RunContext[AgentDeps],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        if not isinstance(request_context.model, DeepSeekChatModel):
            return request_context

        messages, rewritten = rewrite_messages_for_thinking_retry(list(request_context.messages))
        if not rewritten:
            return request_context

        previous = messages[-2]
        assert isinstance(previous, ModelResponse)
        logger.debug(
            "DeepSeek thinking-only output retry for session {}: replaced validation prompt "
            "(thinking preview: {!r})",
            ctx.deps.session_id,
            _thinking_preview(previous),
        )
        return replace(request_context, messages=messages)
