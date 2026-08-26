from __future__ import annotations

import unittest
from datetime import datetime, timezone

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
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


if __name__ == "__main__":
    unittest.main()
