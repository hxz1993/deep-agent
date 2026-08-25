from __future__ import annotations

import unittest

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolAvailabilityDeltaPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from deep_agent.db.event_codec import (
    EventCodecError,
    decode_message_events,
    encode_messages,
    normalize_search_text,
)


class EventCodecTests(unittest.TestCase):
    def test_model_messages_round_trip_as_one_event_per_part(self) -> None:
        messages = [
            ModelRequest(
                parts=[
                    SystemPromptPart(content="system"),
                    UserPromptPart(content="查询 华东 成员"),
                ],
                run_id="run-1",
            ),
            ModelResponse(
                parts=[
                    ThinkingPart(content="private reasoning"),
                    ToolCallPart(
                        tool_name="query_members",
                        args={"parent": "华东"},
                        tool_call_id="call-1",
                    ),
                ],
                model_name="demo-model",
                run_id="run-1",
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="query_members",
                        content={"names": ["上海", "江苏"], "total": 2},
                        tool_call_id="call-1",
                    ),
                    RetryPromptPart(
                        content="retry",
                        tool_name="query_members",
                        tool_call_id="call-1",
                    ),
                    ToolAvailabilityDeltaPart(tools_added=["query_members"]),
                ],
                run_id="run-1",
            ),
            ModelResponse(parts=[TextPart(content="已找到 2 个成员")], run_id="run-1"),
        ]

        drafts = encode_messages(messages, session_id="session-1", run_id="run-1")
        self.assertEqual(len(drafts), 8)
        self.assertEqual(drafts[0].event_type, "model/system_prompt")
        self.assertEqual(drafts[1].event_type, "user/message")
        self.assertEqual(drafts[2].event_type, "assistant/reasoning")
        self.assertIsNone(drafts[2].search_text)
        self.assertEqual(drafts[3].event_type, "tool/call")
        self.assertEqual(drafts[3].call_id, "call-1")
        self.assertEqual(drafts[4].event_type, "tool/result")
        self.assertEqual(drafts[7].event_type, "assistant/text")

        events = [
            {
                "sequence": index + 1,
                "schema_version": draft.schema_version,
                "event_type": draft.event_type,
                "message_id": draft.message_id,
                "part_index": draft.part_index,
                "payload": draft.payload,
            }
            for index, draft in enumerate(drafts)
        ]
        self.assertEqual(decode_message_events(events), messages)

    def test_message_id_is_deterministic(self) -> None:
        messages = [ModelRequest(parts=[UserPromptPart(content="hello")])]
        first = encode_messages(messages, session_id="s", run_id="r")
        second = encode_messages(messages, session_id="s", run_id="r")
        self.assertEqual(first[0].message_id, second[0].message_id)

    def test_unknown_part_fails_closed_on_restore(self) -> None:
        event = {
            "sequence": 1,
            "schema_version": 1,
            "event_type": "model/unknown_part",
            "message_id": "m" * 32,
            "part_index": 0,
            "payload": {
                "message_meta": {"kind": "request", "instructions": None},
                "part": {"part_kind": "future-part"},
            },
        }
        with self.assertRaises(EventCodecError):
            decode_message_events([event])

    def test_search_normalization_supports_chinese_and_nfkc(self) -> None:
        self.assertEqual(normalize_search_text("  ＡＢＣ  华东\n成员  "), "abc 华东 成员")


if __name__ == "__main__":
    unittest.main()
