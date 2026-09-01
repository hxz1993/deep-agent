from __future__ import annotations

import unittest
from datetime import datetime, timezone

from pydantic_ai.messages import (
    ImageUrl,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    NativeToolSearchCallPart,
    NativeToolSearchReturnPart,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolReturnPart,
    ToolSearchCallPart,
    ToolSearchReturnPart,
    UserPromptPart,
)

from deep_agent.agent import events
from deep_agent.agent.history import filter_ephemeral_agent_messages, session_activity_timestamps
from tests.helpers.conversation import project_model_messages


class AgentEventsTests(unittest.TestCase):
    def test_thinking_text_and_tool_search_sse_mapping(self) -> None:
        self.assertEqual(
            events.map_part(ThinkingPart(content="internal reasoning")),
            {"type": "thinking", "delta": "internal reasoning"},
        )
        self.assertEqual(
            events.map_part(ThinkingPartDelta(content_delta="more thought")),
            {"type": "thinking", "delta": "more thought"},
        )
        self.assertEqual(
            events.map_part(TextPart(content="hello")),
            {"type": "text", "delta": "hello"},
        )
        for part in (
            ToolSearchCallPart(args={"queries": ["财务模型"]}, tool_call_id="local"),
            ToolSearchReturnPart(content={"tools": []}, tool_call_id="local"),
            NativeToolSearchCallPart(args={"queries": ["财务模型"]}, tool_call_id="native"),
            NativeToolSearchReturnPart(content={"tools": []}, tool_call_id="native"),
        ):
            self.assertEqual(events.map_part(part)["source"], "tool_search")

    def test_native_web_search_sse_records_execution_and_provider(self) -> None:
        call = NativeToolCallPart(
            "web_search",
            {"query": "DeepAgent"},
            "web-1",
            provider_name="deepseek",
        )
        result = NativeToolReturnPart(
            "web_search",
            {"sources": [{"url": "https://example.com"}]},
            "web-1",
            provider_name="deepseek",
        )

        for payload in (events.map_part(call), events.map_part(result)):
            self.assertEqual(payload["source"], "web")
            self.assertEqual(payload["execution"], "native")
            self.assertEqual(payload["provider"], "deepseek")

    def test_local_web_fetch_sse_records_safe_audit_metadata(self) -> None:
        part = ToolReturnPart(
            "web_fetch",
            {"url": "https://example.com", "content": "body"},
            "fetch-1",
            metadata={
                "web_fetch": {
                    "mode": "local",
                    "elapsed_ms": 12,
                    "truncated": False,
                },
                "sources": [{"url": "https://example.com", "title": "Example"}],
            },
        )

        payload = events.map_part(part)

        self.assertEqual(payload["source"], "web")
        self.assertEqual(payload["execution"], "local")
        self.assertEqual(payload["web"]["elapsed_ms"], 12)
        self.assertEqual(payload["sources"][0]["url"], "https://example.com")

    def test_web_image_sse_exposes_reference_without_base64(self) -> None:
        part = ToolReturnPart(
            "web_image_view",
            ImageUrl(
                url="https://cdn.example.com/chart.png",
                media_type="image/png",
                force_download=True,
            ),
            "image-1",
            metadata={
                "web_image_view": {
                    "mode": "local",
                    "media_type": "image/png",
                    "force_download": True,
                    "ssrf_validated": True,
                },
                "sources": [
                    {"url": "https://cdn.example.com/chart.png", "title": None}
                ],
            },
        )

        payload = events.map_part(part)

        self.assertEqual(payload["source"], "web")
        self.assertEqual(payload["execution"], "local")
        self.assertEqual(payload["result"]["kind"], "web-image-reference")
        self.assertEqual(payload["result"]["url"], "https://cdn.example.com/chart.png")
        self.assertTrue(payload["web"]["force_download"])
        self.assertNotIn("base64", str(payload).lower())

    def test_conversation_projection_merges_thinking_text_and_tool_pair(self) -> None:
        from pydantic_ai.messages import ToolReturnPart

        items = project_model_messages([
            ModelRequest(parts=[UserPromptPart(content="分析预算")]),
            ModelResponse(parts=[
                ThinkingPart(content="先查询"),
                TextPart(content="正在查询"),
                ToolCallPart("lookup", {"region": "华东"}, tool_call_id="call-1"),
            ]),
            ModelRequest(parts=[ToolReturnPart("lookup", {"count": 2}, "call-1")]),
            ModelResponse(parts=[TextPart(content="已完成")]),
        ])

        self.assertEqual(
            [item.kind for item in items],
            ["user_message", "assistant_message", "tool", "assistant_message"],
        )
        self.assertEqual(items[1].thinking, "先查询")
        self.assertEqual(items[1].phase, "process")
        self.assertEqual(items[2].output, {"count": 2})
        self.assertEqual(items[3].phase, "final")

    def test_filter_ephemeral_messages_drops_retry_and_rejected_call(self) -> None:
        messages = [
            ModelResponse(parts=[ThinkingPart(content="internal only")]),
            ModelRequest(parts=[RetryPromptPart(content="Please return text.")]),
            ModelResponse(parts=[
                TextPart(content="正在准备"),
                ToolCallPart("deep_cube__clarify", {}, tool_call_id="invalid"),
            ]),
            ModelRequest(parts=[RetryPromptPart(
                content="fields required",
                tool_name="deep_cube__clarify",
                tool_call_id="invalid",
            )]),
            ModelResponse(parts=[ToolCallPart(
                "deep_cube__clarify",
                {"fields": [{"field": "reason"}]},
                tool_call_id="valid",
            )]),
        ]

        filtered = filter_ephemeral_agent_messages(messages)

        self.assertEqual(len(filtered), 2)
        self.assertEqual(filtered[0].parts[0].content, "正在准备")
        self.assertEqual(filtered[1].parts[0].tool_call_id, "valid")

    def test_session_activity_timestamps(self) -> None:
        early = datetime(2025, 6, 22, 9, 0, tzinfo=timezone.utc)
        late = datetime(2025, 6, 22, 10, 0, tzinfo=timezone.utc)
        created_at, updated_at = session_activity_timestamps([
            ModelRequest(parts=[UserPromptPart(content="hi", timestamp=early)]),
            ModelResponse(parts=[TextPart(content="hello")], timestamp=late),
        ])
        self.assertEqual(created_at, early)
        self.assertEqual(updated_at, late)
        self.assertEqual(session_activity_timestamps([]), (None, None))


if __name__ == "__main__":
    unittest.main()
