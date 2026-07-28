from __future__ import annotations

import unittest
from datetime import datetime, timezone

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from deep_agent.agent import events
from deep_agent.agent.history import (
    filter_ephemeral_agent_messages,
    model_messages_to_history,
    session_activity_timestamps,
)


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

    def test_model_messages_to_history_includes_response_timestamp(self) -> None:
        ts = datetime(2025, 6, 22, 10, 31, tzinfo=timezone.utc)
        response = ModelResponse(parts=[TextPart(content="hello")], timestamp=ts)
        history = model_messages_to_history([response])
        self.assertEqual(history[0].timestamp, ts)

    def test_model_messages_to_history_includes_request_timestamp(self) -> None:
        ts = datetime(2025, 6, 22, 10, 30, tzinfo=timezone.utc)
        request = ModelRequest(parts=[UserPromptPart(content="hi")], timestamp=ts)
        history = model_messages_to_history([request])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].role, "user")
        self.assertEqual(history[0].timestamp, ts)
        self.assertEqual(history[0].branch_point, 0)

    def test_model_messages_to_history_falls_back_to_user_prompt_part_timestamp(self) -> None:
        part_ts = datetime(2025, 6, 22, 9, 0, tzinfo=timezone.utc)
        request = ModelRequest(parts=[UserPromptPart(content="hi", timestamp=part_ts)])
        history = model_messages_to_history([request])
        self.assertEqual(history[0].timestamp, part_ts)

    def test_model_messages_to_history_missing_timestamp(self) -> None:
        request = ModelRequest(
            parts=[UserPromptPart(content="hi", timestamp=None)],
            timestamp=None,
        )
        history = model_messages_to_history([request])
        self.assertIsNone(history[0].timestamp)

    def test_session_activity_timestamps_from_messages(self) -> None:
        early = datetime(2025, 6, 22, 9, 0, tzinfo=timezone.utc)
        late = datetime(2025, 6, 22, 10, 0, tzinfo=timezone.utc)
        messages = [
            ModelRequest(parts=[UserPromptPart(content="hi", timestamp=early)]),
            ModelResponse(parts=[TextPart(content="hello")], timestamp=late),
        ]
        created_at, updated_at = session_activity_timestamps(messages)
        self.assertEqual(created_at, early)
        self.assertEqual(updated_at, late)

    def test_session_activity_timestamps_empty(self) -> None:
        self.assertEqual(session_activity_timestamps([]), (None, None))

    def test_model_messages_to_history_text_only(self) -> None:
        response = ModelResponse(
            parts=[TextPart(content="hello")],
            timestamp=datetime.now(timezone.utc),
        )
        history = model_messages_to_history([response])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].parts[0].type, "text")
        self.assertEqual(history[0].parts[0].content, "hello")

    def test_model_messages_to_history_skips_thinking_only_retry(self) -> None:
        ts = datetime(2025, 6, 22, 10, 45, tzinfo=timezone.utc)
        messages = [
            ModelRequest(parts=[UserPromptPart(content="你好")], timestamp=ts),
            ModelResponse(
                parts=[ThinkingPart(content="你好！有什么我可以帮你的吗？")],
                timestamp=ts,
            ),
            ModelRequest(parts=[RetryPromptPart(content="Please return text or call a tool.")]),
            ModelResponse(parts=[TextPart(content="你好！")], timestamp=ts),
        ]
        history = model_messages_to_history(messages)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].role, "user")
        self.assertEqual(history[1].role, "assistant")
        self.assertEqual(history[1].parts[0].type, "text")
        self.assertEqual(history[1].parts[0].content, "你好！")

    def test_model_messages_to_history_skips_rejected_tool_call(self) -> None:
        messages = [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="deep_cube__clarify",
                        args={},
                        tool_call_id="invalid-call",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    RetryPromptPart(
                        content="fields must be a non-empty array",
                        tool_name="deep_cube__clarify",
                        tool_call_id="invalid-call",
                    )
                ]
            ),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="deep_cube__clarify",
                        args={"fields": [{"id": "reason", "type": "textarea"}]},
                        tool_call_id="valid-call",
                    )
                ]
            ),
        ]

        history = model_messages_to_history(messages)

        self.assertEqual(len(history), 1)
        self.assertEqual(len(history[0].parts), 1)
        self.assertEqual(history[0].parts[0].type, "tool_call")
        self.assertEqual(history[0].parts[0].tool_call_id, "valid-call")

    def test_filter_ephemeral_agent_messages(self) -> None:
        messages = [
            ModelResponse(parts=[ThinkingPart(content="internal only")]),
            ModelRequest(parts=[RetryPromptPart(content="Please return text.")]),
            ModelResponse(parts=[TextPart(content="hello")]),
        ]
        filtered = filter_ephemeral_agent_messages(messages)
        self.assertEqual(len(filtered), 1)
        self.assertIsInstance(filtered[0], ModelResponse)
        self.assertEqual(filtered[0].parts[0].content, "hello")

    def test_filter_ephemeral_agent_messages_drops_rejected_tool_call(self) -> None:
        messages = [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="deep_cube__clarify",
                        args={},
                        tool_call_id="invalid-call",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    RetryPromptPart(
                        content="fields must be a non-empty array",
                        tool_name="deep_cube__clarify",
                        tool_call_id="invalid-call",
                    )
                ]
            ),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="deep_cube__clarify",
                        args={"fields": [{"id": "reason", "type": "textarea"}]},
                        tool_call_id="valid-call",
                    )
                ]
            ),
        ]

        filtered = filter_ephemeral_agent_messages(messages)

        self.assertEqual(len(filtered), 1)
        self.assertIsInstance(filtered[0], ModelResponse)
        self.assertEqual(filtered[0].parts[0].tool_call_id, "valid-call")

    def test_filter_ephemeral_agent_messages_preserves_sibling_parts(self) -> None:
        messages = [
            ModelResponse(
                parts=[
                    TextPart(content="正在准备表单"),
                    ToolCallPart(
                        tool_name="deep_cube__clarify",
                        args={},
                        tool_call_id="invalid-call",
                    ),
                ]
            ),
            ModelRequest(
                parts=[
                    RetryPromptPart(
                        content="fields must be a non-empty array",
                        tool_name="deep_cube__clarify",
                        tool_call_id="invalid-call",
                    )
                ]
            ),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="deep_cube__clarify",
                        args={"fields": [{"field": "reason", "type": "textarea"}]},
                        tool_call_id="valid-call",
                    )
                ]
            ),
        ]

        filtered = filter_ephemeral_agent_messages(messages)

        self.assertEqual(len(filtered), 2)
        self.assertIsInstance(filtered[0], ModelResponse)
        self.assertEqual(len(filtered[0].parts), 1)
        self.assertIsInstance(filtered[0].parts[0], TextPart)
        self.assertEqual(filtered[0].parts[0].content, "正在准备表单")
        self.assertEqual(filtered[1].parts[0].tool_call_id, "valid-call")

    def test_same_tool_call_id_retry_preserves_corrected_call(self) -> None:
        messages = [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="deep_cube__clarify",
                        args={},
                        tool_call_id="same-call",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    RetryPromptPart(
                        content="fields must be a non-empty array",
                        tool_name="deep_cube__clarify",
                        tool_call_id="same-call",
                    )
                ]
            ),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="deep_cube__clarify",
                        args={"fields": [{"field": "reason", "type": "textarea"}]},
                        tool_call_id="same-call",
                    )
                ]
            ),
        ]

        filtered = filter_ephemeral_agent_messages(messages)
        history = model_messages_to_history(messages)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].parts[0].args["fields"][0]["field"], "reason")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].parts[0].tool_call_id, "same-call")
        self.assertEqual(history[0].parts[0].args["fields"][0]["field"], "reason")

    def test_missing_tool_call_id_retry_preserves_only_corrected_call(self) -> None:
        messages = [
            ModelResponse(
                parts=[ToolCallPart("deep_cube__clarify", {}, tool_call_id=None)]
            ),
            ModelRequest(
                parts=[
                    RetryPromptPart(
                        content="fields must be a non-empty array",
                        tool_name="deep_cube__clarify",
                        tool_call_id=None,
                    )
                ]
            ),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "deep_cube__clarify",
                        {"fields": [{"field": "reason", "type": "textarea"}]},
                        tool_call_id=None,
                    )
                ]
            ),
        ]

        filtered = filter_ephemeral_agent_messages(messages)
        history = model_messages_to_history(messages)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].parts[0].args["fields"][0]["field"], "reason")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].parts[0].args["fields"][0]["field"], "reason")

    def test_mixed_tool_result_and_retry_preserves_valid_pair(self) -> None:
        messages = [
            ModelResponse(
                parts=[
                    ToolCallPart("lookup", {"query": "A"}, tool_call_id="lookup-call"),
                    ToolCallPart("deep_cube__clarify", {}, tool_call_id="clarify-call"),
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="lookup",
                        content={"value": "A100"},
                        tool_call_id="lookup-call",
                    ),
                    RetryPromptPart(
                        content="fields must be a non-empty array",
                        tool_name="deep_cube__clarify",
                        tool_call_id="clarify-call",
                    ),
                ]
            ),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "deep_cube__clarify",
                        {"fields": [{"field": "reason", "type": "textarea"}]},
                        tool_call_id="clarify-call-2",
                    )
                ]
            ),
        ]

        filtered = filter_ephemeral_agent_messages(messages)

        self.assertEqual(len(filtered), 3)
        self.assertEqual([part.tool_name for part in filtered[0].parts], ["lookup"])
        self.assertEqual(len(filtered[1].parts), 1)
        self.assertIsInstance(filtered[1].parts[0], ToolReturnPart)
        self.assertEqual(filtered[1].parts[0].tool_call_id, "lookup-call")
        self.assertEqual(filtered[2].parts[0].tool_call_id, "clarify-call-2")

    def test_terminal_retry_is_preserved_for_deferred_resume_but_hidden_from_history(self) -> None:
        messages = [
            ModelResponse(
                parts=[
                    ToolCallPart("builtin__confirm", {"message": "继续？"}, tool_call_id="confirm"),
                    ToolCallPart("deep_cube__clarify", {}, tool_call_id="clarify"),
                ]
            ),
            ModelRequest(
                parts=[
                    RetryPromptPart(
                        content="fields must be a non-empty array",
                        tool_name="deep_cube__clarify",
                        tool_call_id="clarify",
                    )
                ]
            ),
        ]

        filtered = filter_ephemeral_agent_messages(messages)
        history = model_messages_to_history(messages)

        self.assertEqual(len(filtered), 2)
        self.assertEqual(len(filtered[0].parts), 2)
        self.assertIsInstance(filtered[1].parts[0], RetryPromptPart)
        self.assertEqual(len(history), 1)
        self.assertEqual(len(history[0].parts), 1)
        self.assertEqual(history[0].parts[0].tool_call_id, "confirm")

    def test_reused_old_tool_call_id_only_drops_latest_rejected_call(self) -> None:
        messages = [
            ModelResponse(parts=[ToolCallPart("lookup", {"query": "A"}, tool_call_id="same")]),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="lookup",
                        content={"value": "A100"},
                        tool_call_id="same",
                    )
                ]
            ),
            ModelResponse(
                parts=[ToolCallPart("deep_cube__clarify", {}, tool_call_id="same")]
            ),
            ModelRequest(
                parts=[
                    RetryPromptPart(
                        content="fields must be a non-empty array",
                        tool_name="deep_cube__clarify",
                        tool_call_id="same",
                    )
                ]
            ),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "deep_cube__clarify",
                        {"fields": [{"field": "reason", "type": "textarea"}]},
                        tool_call_id="same",
                    )
                ]
            ),
        ]

        filtered = filter_ephemeral_agent_messages(messages)
        history = model_messages_to_history(messages)

        self.assertEqual(len(filtered), 3)
        self.assertEqual(filtered[0].parts[0].tool_name, "lookup")
        self.assertIsInstance(filtered[1].parts[0], ToolReturnPart)
        self.assertEqual(filtered[2].parts[0].tool_name, "deep_cube__clarify")
        tool_calls = [
            part
            for message in history
            for part in message.parts
            if part.type == "tool_call"
        ]
        self.assertEqual([part.tool for part in tool_calls], ["lookup", "deep_cube__clarify"])


if __name__ == "__main__":
    unittest.main()
