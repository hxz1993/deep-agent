from __future__ import annotations

import unittest

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ThinkingPart, ToolCallPart, ToolReturnPart

from deep_agent.conversation.projector import project_session_events
from deep_agent.db.run_progress import encode_run_progress


def checkpoint(messages, *, status="running"):
    return {"event_id": "checkpoint", "session_id": "session", "sequence": 5,
            "schema_version": 1, "event_type": "run/progress", "status": status,
            "run_id": "run", "turn_id": "turn", "created_at": "2026-09-08T00:00:00Z",
            "updated_at": "2026-09-08T00:00:01Z",
            "payload": encode_run_progress(messages, first_message_ordinal=1, revision=1)}


class RunProgressProjectionTests(unittest.TestCase):
    def test_all_loops_keep_order_and_only_current_status_is_pending(self):
        messages = []
        for index in range(12):
            messages.extend([
                ModelResponse(parts=[TextPart(f"round-{index}"), ToolCallPart("read", {}, f"call-{index}")]),
                ModelRequest(parts=[ToolReturnPart("read", f"result-{index}", f"call-{index}")]),
            ])
        messages.append(ModelResponse(parts=[TextPart("still writing")]))
        history = project_session_events([checkpoint(messages)])
        assistants = [item for item in history.items if item.kind == "assistant_message"]
        self.assertEqual([item.text for item in assistants], [*(f"round-{index}" for index in range(12)), "still writing"])
        self.assertTrue(all(item.phase == "process" and item.status == "pending" for item in assistants))
        self.assertEqual(history.event_head_sequence, 5)
        self.assertEqual(len({item.item_id for item in history.items}), len(history.items))
        self.assertTrue(all(item.order.sequence == 5 and item.source_event_ids == ["checkpoint"] for item in history.items))
        self.assertTrue(all(item.status == "completed" for item in history.items if item.kind == "tool"))

    def test_unfinished_ui_preview_cannot_become_actionable(self):
        response = ModelResponse(parts=[ToolCallPart("confirm", {"question": "?"}, "ui")])
        event = checkpoint([response])
        event["payload"]["messages"][0]["parts"][0]["metadata"] = {"widget": "confirm"}
        item = project_session_events([event]).items[0]
        self.assertEqual(item.status, "pending")
        self.assertEqual(item.mode, "tool")
        self.assertIsNone(item.widget)
        self.assertIsNone(item.metadata)

    def test_terminal_checkpoint_receipts_never_duplicate_canonical_history(self):
        for status in ("completed", "pending", "interrupted", "failed", "cancelled"):
            with self.subTest(status=status):
                history = project_session_events([checkpoint([ModelResponse(parts=[TextPart("old preview")])], status=status)])
                self.assertEqual(history.items, [])
                self.assertEqual(history.event_head_sequence, 5)

    def test_partial_thinking_remains_visible_without_becoming_final(self):
        item = project_session_events([checkpoint([ModelResponse(parts=[ThinkingPart("partial thought")])])]).items[0]
        self.assertEqual(item.thinking, "partial thought")
        self.assertEqual((item.phase, item.status), ("process", "pending"))

    def test_repeated_same_sequence_snapshot_replaces_not_appends_preview(self):
        first = project_session_events([checkpoint([ModelResponse(parts=[TextPart("first")])])])
        second = project_session_events([checkpoint([ModelResponse(parts=[TextPart("first updated")])])])
        self.assertEqual(first.items[0].item_id, second.items[0].item_id)
        self.assertEqual(second.items[0].text, "first updated")
        self.assertEqual(len(second.items), 1)
