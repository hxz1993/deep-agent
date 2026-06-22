from __future__ import annotations

import unittest
from datetime import datetime, timezone

from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart, ToolCallPart
from pydantic_ai.providers.deepseek import DeepSeekProvider

from deep_agent.agent.models import DeepSeekChatModel
from deep_agent.agent.models.deepseek_chat import _promote_thinking_only_parts


class DeepSeekChatModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = DeepSeekChatModel(
            "deepseek-v4-flash",
            provider=DeepSeekProvider(api_key="sk-test"),
        )

    def test_map_thinking_only_response_fills_content(self) -> None:
        response = ModelResponse(
            parts=[
                ThinkingPart(
                    content="你好！有什么我可以帮你的吗？",
                    id="reasoning_content",
                    provider_name="deepseek",
                )
            ],
            timestamp=datetime.now(timezone.utc),
        )

        message_param = self.model._map_model_response(response)

        self.assertIsNotNone(message_param)
        assert message_param is not None
        self.assertEqual(message_param["content"], "你好！有什么我可以帮你的吗？")
        self.assertEqual(message_param["reasoning_content"], "你好！有什么我可以帮你的吗？")

    def test_map_text_response_unchanged(self) -> None:
        response = ModelResponse(
            parts=[TextPart(content="你好！")],
            timestamp=datetime.now(timezone.utc),
        )

        message_param = self.model._map_model_response(response)

        self.assertIsNotNone(message_param)
        assert message_param is not None
        self.assertEqual(message_param["content"], "你好！")
        self.assertNotIn("reasoning_content", message_param)

    def test_promote_thinking_only_parts(self) -> None:
        promoted = _promote_thinking_only_parts(
            [ThinkingPart(content="你好！有什么我可以帮你的吗？")]
        )
        self.assertEqual(len(promoted), 1)
        self.assertIsInstance(promoted[0], TextPart)
        self.assertEqual(promoted[0].content, "你好！有什么我可以帮你的吗？")

    def test_promote_thinking_only_parts_keeps_mixed_response(self) -> None:
        parts = [
            ThinkingPart(content="internal"),
            TextPart(content="你好！"),
        ]
        self.assertIs(_promote_thinking_only_parts(parts), parts)

    def test_promote_thinking_only_parts_keeps_tool_calls(self) -> None:
        parts = [
            ThinkingPart(content="call the tool"),
            ToolCallPart("get_current_time", {}, tool_call_id="call_1"),
        ]
        self.assertIs(_promote_thinking_only_parts(parts), parts)

    def test_process_response_promotes_reasoning_only_completion(self) -> None:
        completion = ChatCompletion(
            id="chatcmpl-test",
            choices=[
                Choice(
                    finish_reason="stop",
                    index=0,
                    message=ChatCompletionMessage(
                        role="assistant",
                        content=None,
                        reasoning_content="你好！有什么我可以帮你的吗？",
                    ),
                )
            ],
            created=1710000000,
            model="deepseek-v4-flash",
            object="chat.completion",
        )

        model_response = self.model._process_response(completion)

        self.assertEqual(len(model_response.parts), 1)
        self.assertIsInstance(model_response.parts[0], TextPart)
        self.assertEqual(model_response.parts[0].content, "你好！有什么我可以帮你的吗？")


if __name__ == "__main__":
    unittest.main()
