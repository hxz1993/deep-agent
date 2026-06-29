from __future__ import annotations

import unittest

from pydantic_ai.messages import ModelResponse, TextPart

from deep_agent.agent.stream_accumulator import StreamAccumulator


class StreamAccumulatorTests(unittest.TestCase):
    def test_text_snapshot(self) -> None:
        acc = StreamAccumulator()
        acc.ingest({"type": "text", "delta": "hello "})
        acc.ingest({"type": "text", "delta": "world"})
        messages = acc.to_model_messages()
        self.assertEqual(len(messages), 1)
        response = messages[0]
        self.assertIsInstance(response, ModelResponse)
        assert isinstance(response, ModelResponse)
        self.assertEqual(response.state, "interrupted")
        text_parts = [p for p in response.parts if isinstance(p, TextPart)]
        self.assertEqual(text_parts[0].content, "hello world")

    def test_incomplete_tool_call_excluded(self) -> None:
        acc = StreamAccumulator()
        acc.ingest(
            {
                "type": "tool_call",
                "tool": "confirm_action",
                "args": {},
                "tool_call_id": "c1",
            }
        )
        messages = acc.to_model_messages()
        self.assertEqual(messages, [])

    def test_complete_tool_call_included(self) -> None:
        acc = StreamAccumulator()
        acc.ingest(
            {
                "type": "tool_call",
                "tool": "confirm_action",
                "args": {},
                "tool_call_id": "c1",
            }
        )
        acc.ingest(
            {
                "type": "tool_call",
                "tool": "confirm_action",
                "args": {"message": "ok?"},
                "tool_call_id": "c1",
            }
        )
        messages = acc.to_model_messages()
        self.assertEqual(len(messages), 1)
        response = messages[0]
        assert isinstance(response, ModelResponse)
        self.assertEqual(len(response.parts), 1)


if __name__ == "__main__":
    unittest.main()
