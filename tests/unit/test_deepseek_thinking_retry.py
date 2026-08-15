from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest import mock

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.providers.deepseek import DeepSeekProvider

from deep_agent.agent.models.deepseek_thinking_retry import (
    DEEPSEEK_THINKING_RETRY_PROMPT,
    DeepSeekThinkingRetry,
    rewrite_messages_for_thinking_retry,
)
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.models import DeepSeekChatModel


class DeepSeekThinkingRetryTests(unittest.IsolatedAsyncioTestCase):
    def test_rewrites_retry_after_thinking_only_response(self) -> None:
        ts = datetime.now(timezone.utc)
        messages = [
            ModelRequest(parts=[UserPromptPart(content="你好")], timestamp=ts),
            ModelResponse(parts=[ThinkingPart(content="你好！")], timestamp=ts),
            ModelRequest(parts=[RetryPromptPart(content="Please return text or call a tool.")]),
        ]

        rewritten, changed = rewrite_messages_for_thinking_retry(messages)

        self.assertTrue(changed)
        retry = rewritten[-1]
        assert isinstance(retry, ModelRequest)
        part = retry.parts[0]
        assert isinstance(part, RetryPromptPart)
        self.assertEqual(part.content, DEEPSEEK_THINKING_RETRY_PROMPT)

    def test_keeps_retry_after_text_response(self) -> None:
        ts = datetime.now(timezone.utc)
        messages = [
            ModelResponse(parts=[TextPart(content="你好！")], timestamp=ts),
            ModelRequest(parts=[RetryPromptPart(content="Please return text or call a tool.")]),
        ]

        rewritten, changed = rewrite_messages_for_thinking_retry(messages)

        self.assertFalse(changed)
        self.assertEqual(rewritten, messages)

    def test_keeps_tool_validation_retry(self) -> None:
        ts = datetime.now(timezone.utc)
        messages = [
            ModelResponse(parts=[ThinkingPart(content="bad tool args")], timestamp=ts),
            ModelRequest(
                parts=[
                    RetryPromptPart(
                        content="validation failed",
                        tool_name="get_current_time",
                    )
                ]
            ),
        ]

        _, changed = rewrite_messages_for_thinking_retry(messages)
        self.assertFalse(changed)

    async def test_before_model_request_rewrites_without_thinking_setting(self) -> None:
        ts = datetime.now(timezone.utc)
        model = DeepSeekChatModel("deepseek-v4-flash", provider=DeepSeekProvider(api_key="sk-test"))
        messages = [
            ModelRequest(parts=[UserPromptPart(content="你好")], timestamp=ts),
            ModelResponse(parts=[ThinkingPart(content="你好！")], timestamp=ts),
            ModelRequest(parts=[RetryPromptPart(content="Please return text or call a tool.")]),
        ]
        request_context = ModelRequestContext(
            model=model,
            messages=messages,
            model_settings=None,
            model_request_parameters=mock.Mock(),
        )
        run_context = mock.Mock()
        run_context.deps = AgentDeps(session_id="sess-1")

        with mock.patch("deep_agent.agent.models.deepseek_thinking_retry.logger") as log:
            updated = await DeepSeekThinkingRetry().before_model_request(run_context, request_context)

        retry = updated.messages[-1]
        assert isinstance(retry, ModelRequest)
        part = retry.parts[0]
        assert isinstance(part, RetryPromptPart)
        self.assertEqual(part.content, DEEPSEEK_THINKING_RETRY_PROMPT)
        self.assertIsNone(updated.model_settings)
        log.debug.assert_called_once()


if __name__ == "__main__":
    unittest.main()
