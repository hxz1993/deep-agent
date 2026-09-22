from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import unittest

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart

from tests.unit import test_session_event_store as fixture
from tests.unit.test_context_compaction import valid_summary


class CompactionPersistedTailTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.SessionEventStoreTests.asyncSetUp(self)
        self.raw = [
            ModelRequest(parts=[UserPromptPart("old")]), ModelResponse(parts=[TextPart("old answer")]),
            ModelRequest(parts=[UserPromptPart("current")]),
            ModelResponse(parts=[ToolCallPart("read", {}, "call")]),
            ModelRequest(parts=[ToolReturnPart("read", {"value": 130}, "call")]),
            ModelResponse(parts=[TextPart("current answer")]),
        ]
        self.rows = await self.store.append("tail", self.raw, agent_id=self.agent_id, user_id="user-1", run_id="run-tail")

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def persist(self, tail):
        summary = valid_summary()
        return await self.store.persist_compaction_projection(
            "tail", agent_id=self.agent_id, user_id="user-1", raw_history=self.raw,
            projected_history=[ModelRequest(parts=[UserPromptPart("Summary of previous conversation:\n\n"+summary)]), *tail],
            summary_markdown=summary, trigger="pressure", model_id="model",
            estimated_before=100, estimated_after=50, target_tokens=60,
            context_window_tokens=1000, preserved_tail_tokens=25, duration_ms=10,
        )

    async def test_runtime_request_shell_does_not_expand_compaction_cutoff(self):
        tail = list(self.raw[2:])
        tail[2] = replace(tail[2], run_id="runtime-run", conversation_id="runtime-conversation",
                          instructions="runtime instructions", timestamp=datetime.now(timezone.utc))
        event = await self.persist(tail)
        self.assertIsNotNone(event)
        self.assertEqual(event.payload["source"]["through_sequence"], self.rows[1].sequence)
        visible = await self.store.get_history("tail", agent_id=self.agent_id)
        self.assertEqual(visible[1:], self.raw[2:])

    async def test_changed_tool_result_cannot_silently_consume_current_turn(self):
        tail = list(self.raw[2:])
        tail[2] = replace(tail[2], parts=[ToolReturnPart("read", {"value": 999}, "call")])
        with self.assertRaisesRegex(ValueError, "compaction_tail_mismatch"):
            await self.persist(tail)
        self.assertEqual(await self.store.get_history("tail", agent_id=self.agent_id), self.raw)

    async def test_changed_tool_call_identity_is_rejected(self):
        tail = list(self.raw[2:])
        tail[2] = replace(tail[2], parts=[ToolReturnPart("read", {"value": 130}, "other-call")])
        with self.assertRaisesRegex(ValueError, "compaction_tail_mismatch"):
            await self.persist(tail)
        self.assertEqual(await self.store.get_history("tail", agent_id=self.agent_id), self.raw)

    def test_existing_instructions_cannot_be_replaced(self):
        from deep_agent.db.conversation import _common_message_suffix
        original = ModelRequest(parts=[UserPromptPart("same")], instructions="original")
        changed = replace(original, instructions="changed")
        self.assertEqual(_common_message_suffix([original], [changed]), 0)
