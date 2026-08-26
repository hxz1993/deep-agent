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
    def test_model_messages_round_trip_as_one_event_per_message(self) -> None:
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
        self.assertEqual(len(drafts), 4)
        self.assertEqual(
            [draft.event_type for draft in drafts],
            ["user/message", "assistant/message", "tool/results", "assistant/message"],
        )
        self.assertNotIn("private reasoning", drafts[1].search_text or "")
        self.assertIn("query_members", drafts[1].search_text or "")
        self.assertIn("上海", drafts[2].search_text or "")

        events = [
            {
                "sequence": index + 1,
                "event_id": draft.event_id,
                "schema_version": draft.schema_version,
                "event_type": draft.event_type,
                "payload": draft.payload,
            }
            for index, draft in enumerate(drafts)
        ]
        self.assertEqual(decode_message_events(events), messages)

    def test_event_id_is_deterministic(self) -> None:
        messages = [ModelRequest(parts=[UserPromptPart(content="hello")])]
        first = encode_messages(messages, session_id="s", run_id="r")
        second = encode_messages(messages, session_id="s", run_id="r")
        self.assertEqual(first[0].event_id, second[0].event_id)

    def test_unknown_part_fails_closed_on_restore(self) -> None:
        event = {
            "sequence": 1,
            "schema_version": 2,
            "event_type": "model/request",
            "payload": {
                "message": {
                    "kind": "request",
                    "parts": [{"part_kind": "future-part"}],
                },
            },
        }
        with self.assertRaises(EventCodecError):
            decode_message_events([event])

    def test_search_normalization_supports_chinese_and_nfkc(self) -> None:
        self.assertEqual(normalize_search_text("  ＡＢＣ  华东\n成员  "), "abc 华东 成员")

    def test_search_projection_excludes_reasoning_and_sensitive_metadata(self) -> None:
        message = ModelResponse(
            parts=[ThinkingPart(content="不能检索的私有推理"), TextPart(content="公开答案")],
            provider_details={
                "authorization": "Bearer secret",
                "headers": {"cookie": "private"},
            },
        )
        draft = encode_messages([message], session_id="safe", run_id="run-safe")[0]
        self.assertIn("公开答案", draft.search_text or "")
        self.assertNotIn("不能检索的私有推理", draft.search_text or "")
        self.assertNotIn("secret", draft.search_text or "")
        self.assertNotIn("private", draft.search_text or "")


if __name__ == "__main__":
    unittest.main()
