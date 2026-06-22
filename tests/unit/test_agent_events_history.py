from __future__ import annotations

import unittest
from datetime import datetime, timezone

from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart, ThinkingPartDelta

from deep_agent.agent import events
from deep_agent.memory.history import model_messages_to_history


class AgentEventsThinkingTests(unittest.TestCase):
    def test_map_thinking_part(self) -> None:
        result = events.map_part(ThinkingPart(content="internal reasoning"))
        self.assertEqual(result, {"type": "thinking", "delta": "internal reasoning"})

    def test_map_thinking_part_empty(self) -> None:
        self.assertIsNone(events.map_part(ThinkingPart(content="")))

    def test_map_thinking_part_delta(self) -> None:
        result = events.map_part(ThinkingPartDelta(content_delta="more thought"))
        self.assertEqual(result, {"type": "thinking", "delta": "more thought"})

    def test_map_text_part_unchanged(self) -> None:
        result = events.map_part(TextPart(content="hello"))
        self.assertEqual(result, {"type": "text", "delta": "hello"})


class HistoryThinkingTests(unittest.TestCase):
    def test_model_messages_to_history_splits_thinking_and_text(self) -> None:
        response = ModelResponse(
            parts=[
                ThinkingPart(content="用户用中文打招呼。"),
                TextPart(content="你好！"),
            ],
            timestamp=datetime.now(timezone.utc),
        )
        history = model_messages_to_history([response])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].role, "assistant")
        self.assertEqual(len(history[0].parts), 2)
        self.assertEqual(history[0].parts[0].type, "thinking")
        self.assertEqual(history[0].parts[0].content, "用户用中文打招呼。")
        self.assertEqual(history[0].parts[1].type, "text")
        self.assertEqual(history[0].parts[1].content, "你好！")

    def test_model_messages_to_history_text_only(self) -> None:
        response = ModelResponse(
            parts=[TextPart(content="hello")],
            timestamp=datetime.now(timezone.utc),
        )
        history = model_messages_to_history([response])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].parts[0].type, "text")
        self.assertEqual(history[0].parts[0].content, "hello")


if __name__ == "__main__":
    unittest.main()
