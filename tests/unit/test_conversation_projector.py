from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import datetime, timezone

from pydantic_ai.messages import (
    ImageUrl,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    NativeToolSearchCallPart,
    NativeToolSearchReturnPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from deep_agent.agent.planning import PLAN_METADATA_KEY
from deep_agent.conversation.projector import project_session_events
from deep_agent.db.serialization import serialize_message


NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def event(
    sequence: int,
    event_type: str,
    *,
    message: object | None = None,
    status: str | None = None,
    run_id: str = "run-1",
    turn_id: str = "run-1",
) -> dict[str, object]:
    payload = {"message": serialize_message(message)} if message is not None else {}
    return {
        "event_id": f"event-{sequence}",
        "session_id": "session-1",
        "sequence": sequence,
        "schema_version": 1,
        "event_type": event_type,
        "status": status,
        "turn_id": turn_id,
        "run_id": run_id,
        "payload": payload,
        "created_at": NOW,
        "updated_at": NOW,
    }


class ConversationProjectorTests(unittest.TestCase):
    def test_interleaved_native_search_keeps_final_text_after_tools(self) -> None:
        native = event(1, "assistant/message", status="completed", message=ModelResponse(parts=[
            ThinkingPart(content="准备查询"), TextPart(content="先搜索"),
            NativeToolCallPart(tool_name="web_search", args={}, tool_call_id="native-1"),
            NativeToolReturnPart(tool_name="web_search", content={}, tool_call_id="native-1"),
            ThinkingPart(content="继续分析"), TextPart(content="补充搜索"),
            NativeToolCallPart(tool_name="web_search", args={}, tool_call_id="native-2"),
            NativeToolCallPart(tool_name="web_search", args={}, tool_call_id="native-3"),
            NativeToolReturnPart(tool_name="web_search", content={}, tool_call_id="native-2"),
            NativeToolReturnPart(tool_name="web_search", content={}, tool_call_id="native-3"),
            ThinkingPart(content="整理结果"), TextPart(content="最终回答"),
        ]))
        before = deepcopy(native)
        history = project_session_events([native])
        self.assertEqual([item.kind for item in history.items], [
            "assistant_message", "tool", "assistant_message", "tool", "tool", "assistant_message",
        ])
        assistants = [item for item in history.items if item.kind == "assistant_message"]
        self.assertEqual([item.text for item in assistants], ["先搜索", "补充搜索", "最终回答"])
        self.assertEqual([item.thinking for item in assistants], ["准备查询", "继续分析", "整理结果"])
        self.assertEqual([item.phase for item in assistants], ["process", "process", "final"])
        self.assertEqual([item.order.part_offset for item in assistants], [0, 4, 10])
        self.assertEqual(len({item.item_id for item in history.items}), len(history.items))
        self.assertEqual(assistants[0].item_id, "message:event-1")
        self.assertEqual(sum(item.usage is not None for item in assistants), 1)
        self.assertTrue(all(item.source_event_ids == ["event-1"] for item in assistants))
        self.assertEqual(native, before)
        self.assertEqual(history, project_session_events([native]))

    def test_native_search_before_final_with_empty_thinking(self) -> None:
        history = project_session_events([event(1, "assistant/message", status="completed", message=ModelResponse(parts=[
            ThinkingPart(content=""), TextPart(content="查询中"),
            NativeToolCallPart(tool_name="web_search", args={}, tool_call_id="native-1"),
            NativeToolReturnPart(tool_name="web_search", content={}, tool_call_id="native-1"),
            ThinkingPart(content=""), TextPart(content="最终回答"),
        ]))])
        self.assertEqual([item.order.part_offset for item in history.items], [1, 2, 5])
        self.assertEqual(history.items[-1].phase, "final")
        self.assertEqual(history.items[-1].text, "最终回答")

    def test_pending_tool_after_intermediate_text_has_no_final(self) -> None:
        history = project_session_events([event(1, "assistant/message", status="pending", message=ModelResponse(parts=[
            TextPart(content="查询中"),
            NativeToolCallPart(tool_name="web_search", args={}, tool_call_id="native-1"),
            NativeToolReturnPart(tool_name="web_search", content={}, tool_call_id="native-1"),
            TextPart(content="请确认"),
            ToolCallPart(tool_name="builtin__confirm", args={}, tool_call_id="confirm-1"),
        ]))])
        self.assertFalse(any(getattr(item, "phase", None) == "final" for item in history.items))
        self.assertEqual(history.items[-1].status, "awaiting_input")

    def test_projects_messages_tools_plan_and_safe_user_context(self) -> None:
        plan = {
            "items": [{"id": "one", "content": "读取数据", "status": "completed"}]
        }
        events = [
            event(
                1,
                "user/message",
                message=ModelRequest(parts=[UserPromptPart(content=[
                    {"kind": "text-content", "content": "分析预算"},
                    {"kind": "attachment-ref", "id": "file-1", "name": "预算.xlsx", "mime": "application/vnd.ms-excel"},
                    {"kind": "context-ref", "key": "range", "type": "deeptable.range", "label": "预算!B2:C4", "metadata": {"token": "secret"}},
                ])]),
            ),
            event(
                2,
                "assistant/message",
                status="pending",
                message=ModelResponse(parts=[
                    ThinkingPart(content="先查询"),
                    TextPart(content="我先读取数据"),
                    ToolCallPart(tool_name="lookup", args={"region": "华东"}, tool_call_id="call-1"),
                    ToolCallPart(tool_name="write_plan", args={"items": plan["items"]}, tool_call_id="plan-1"),
                ]),
            ),
            event(
                3,
                "tool/results",
                message=ModelRequest(parts=[
                    ToolReturnPart(tool_name="lookup", content={"count": 2}, tool_call_id="call-1"),
                    ToolReturnPart(tool_name="write_plan", content={"ok": True}, tool_call_id="plan-1", metadata={PLAN_METADATA_KEY: plan}),
                ]),
            ),
            event(
                4,
                "assistant/message",
                status="completed",
                message=ModelResponse(parts=[TextPart(content="分析完成")]),
            ),
            event(5, "compaction", status="completed"),
            event(6, "session/title", status="completed"),
        ]

        projected = project_session_events(events)

        self.assertEqual(projected.schema_version, 1)
        self.assertEqual(projected.event_head_sequence, 6)
        self.assertEqual(
            [item.kind for item in projected.items],
            ["user_message", "assistant_message", "tool", "plan", "assistant_message"],
        )
        user = projected.items[0]
        self.assertEqual(user.text, "分析预算")
        self.assertEqual(user.attachments[0].name, "预算.xlsx")
        self.assertEqual(user.context_refs[0]["label"], "预算!B2:C4")
        self.assertNotIn("metadata", user.context_refs[0])

        first_assistant = projected.items[1]
        self.assertEqual(first_assistant.thinking, "先查询")
        self.assertEqual(first_assistant.text, "我先读取数据")
        self.assertEqual(first_assistant.phase, "process")
        tool = projected.items[2]
        self.assertEqual(tool.item_id, "tool:call-1")
        self.assertEqual(tool.output, {"count": 2})
        self.assertEqual(tool.status, "completed")
        plan_item = projected.items[3]
        self.assertEqual(plan_item.item_id, "plan:plan-1")
        self.assertEqual(plan_item.completed, 1)
        self.assertEqual(projected.items[4].phase, "final")

    def test_pending_deferred_and_terminal_interruption(self) -> None:
        pending = event(
            1,
            "assistant/message",
            status="pending",
            message=ModelResponse(parts=[ToolCallPart(
                tool_name="builtin__confirm",
                args={"message": "继续？"},
                tool_call_id="confirm-1",
            )]),
        )
        terminal = event(2, "run/terminal", status="interrupted")

        tool = project_session_events([pending, terminal]).items[0]

        self.assertEqual(tool.kind, "tool")
        self.assertEqual(tool.mode, "deferred")
        self.assertEqual(tool.status, "interrupted")

    def test_interrupted_assistant_closes_unfinished_tool_in_same_run(self) -> None:
        interrupted = event(
            1,
            "assistant/message",
            status="interrupted",
            message=ModelResponse(
                parts=[
                    TextPart(content="正在导出报告"),
                    ToolCallPart(
                        tool_name="export_file_to_download_center",
                        args={"name": "报告.xlsx"},
                        tool_call_id="export-1",
                    ),
                ],
                state="interrupted",
            ),
        )

        projected = project_session_events([interrupted])
        tool = next(item for item in projected.items if item.kind == "tool")

        self.assertEqual(tool.status, "interrupted")

    def test_native_call_and_result_in_same_response_form_one_tool(self) -> None:
        native = event(
            1,
            "assistant/message",
            message=ModelResponse(parts=[
                NativeToolSearchCallPart(args={"queries": ["预算"]}, tool_call_id="native-1"),
                NativeToolSearchReturnPart(content={"tools": {}}, tool_call_id="native-1"),
            ]),
        )

        tool = project_session_events([native]).items[0]

        self.assertEqual(tool.item_id, "tool:native-1")
        self.assertEqual(tool.mode, "tool_search")
        self.assertEqual(tool.status, "completed")
        self.assertEqual(tool.output, {"tools": {}})

    def test_provider_web_search_preserves_safe_audit_metadata(self) -> None:
        events = [
            event(
                1,
                "assistant/message",
                message=ModelResponse(parts=[
                    ToolCallPart(
                        "web_search",
                        {"query": "DeepAgent"},
                        tool_call_id="web-provider-1",
                    )
                ]),
            ),
            event(
                2,
                "tool/results",
                message=ModelRequest(parts=[
                    ToolReturnPart(
                        "web_search",
                        {"provider": "tavily", "result_count": 1},
                        "web-provider-1",
                        metadata={
                            "web_search": {
                                "mode": "provider",
                                "provider": "tavily",
                                "elapsed_ms": 42,
                                "truncated": False,
                            },
                            "sources": [
                                {"url": "https://example.com", "title": "Example"}
                            ],
                        },
                    )
                ]),
            ),
        ]

        tool = project_session_events(events).items[0]

        self.assertEqual(tool.metadata["web_search"]["provider"], "tavily")
        self.assertEqual(tool.metadata["web_search"]["elapsed_ms"], 42)
        self.assertEqual(tool.metadata["sources"][0]["url"], "https://example.com")

    def test_web_fetch_preserves_safe_audit_metadata(self) -> None:
        events = [
            event(
                1,
                "assistant/message",
                message=ModelResponse(parts=[
                    ToolCallPart(
                        "web_fetch",
                        {"url": "https://example.com"},
                        tool_call_id="web-fetch-1",
                    )
                ]),
            ),
            event(
                2,
                "tool/results",
                message=ModelRequest(parts=[
                    ToolReturnPart(
                        "web_fetch",
                        {"content": "body"},
                        "web-fetch-1",
                        metadata={
                            "web_fetch": {
                                "mode": "local",
                                "elapsed_ms": 17,
                                "truncated": False,
                            },
                            "sources": [{"url": "https://example.com", "title": None}],
                        },
                    )
                ]),
            ),
        ]

        tool = project_session_events(events).items[0]

        self.assertEqual(tool.metadata["web_fetch"]["elapsed_ms"], 17)
        self.assertEqual(tool.metadata["sources"][0]["url"], "https://example.com")

    def test_web_image_history_projects_safe_preview_reference(self) -> None:
        events = [
            event(
                1,
                "assistant/message",
                message=ModelResponse(parts=[
                    ToolCallPart(
                        "web_image_view",
                        {"url": "https://cdn.example.com/chart.png"},
                        tool_call_id="image-1",
                    )
                ]),
            ),
            event(
                2,
                "tool/results",
                message=ModelRequest(parts=[
                    ToolReturnPart(
                        "web_image_view",
                        ImageUrl(
                            url="https://cdn.example.com/chart.png",
                            media_type="image/png",
                            force_download=False,
                        ),
                        "image-1",
                        metadata={
                            "web_image_view": {
                                "mode": "local",
                                "media_type": "image/png",
                                "force_download": False,
                                "ssrf_validated": True,
                            },
                            "sources": [
                                {
                                    "url": "https://cdn.example.com/chart.png",
                                    "title": None,
                                }
                            ],
                        },
                    )
                ]),
            ),
        ]

        tool = project_session_events(events).items[0]

        self.assertEqual(tool.output["kind"], "web-image-reference")
        self.assertEqual(tool.output["url"], "https://cdn.example.com/chart.png")
        self.assertEqual(tool.metadata["web_image_view"]["media_type"], "image/png")
        self.assertNotIn("base64", str(tool.model_dump()).lower())


if __name__ == "__main__":
    unittest.main()
