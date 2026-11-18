from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import unittest

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from sqlalchemy import event as sql_event

from deep_agent.agent.context.compaction import SummaryUnavailable
from deep_agent.agent.runner import _compaction_source_view
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
            estimated_before=100, estimated_after=50, budget_limit_tokens=60,
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

    async def test_paired_read_is_scoped_and_keeps_independent_raw_and_projected_facts(self):
        async def read(agent_id):
            statements = []

            def record(_conn, _cursor, statement, *_):
                statements.append(statement)

            sql_event.listen(self.engine.sync_engine, "before_cursor_execute", record)
            try:
                result = await self.store.get_history_pair("tail", agent_id=agent_id)
            finally:
                sql_event.remove(self.engine.sync_engine, "before_cursor_execute", record)
            self.assertEqual(len(statements), 1)
            return result

        self.assertEqual(await read("another-agent"), ([], []))
        for cutoff in (0, 2):
            with self.subTest(cutoff=cutoff):
                if cutoff:
                    self.assertIsNotNone(await self.persist(self.raw[cutoff:]))
                raw, visible = await read(self.agent_id)
                self.assertEqual(raw, self.raw)
                previous_view = await self.store.get_history("tail", agent_id=self.agent_id)
                if cutoff:
                    # Each render generates a fresh timestamp for the summary.
                    self.assertEqual(visible[0].parts[0].content, previous_view[0].parts[0].content)
                    self.assertEqual(visible[1:], previous_view[1:])
                else:
                    self.assertEqual(visible, previous_view)
                visible[-2].parts[0].content["value"] = 999
                self.assertEqual(raw[-2].parts[0].content, {"value": 130})

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

    def test_regrouped_user_prompts_keep_content_order_instructions_and_tool_facts(self):
        first = ModelRequest(parts=[UserPromptPart("first")], metadata={"reasoning_effort": "high"})
        second = ModelRequest(parts=[UserPromptPart("second")], instructions="use saved evidence")
        tool = ModelRequest(parts=[ToolReturnPart("read", {"value": 130}, "call")])
        source = [first, second, self.raw[3], tool]
        merged = replace(second, parts=[*first.parts, *second.parts])
        wire = [merged, self.raw[3], tool]
        self.assertIs(_compaction_source_view(source, wire), source)
        for changed in (
            [replace(merged, parts=list(reversed(merged.parts))), *wire[1:]],
            [replace(merged, parts=[first.parts[0], UserPromptPart("different")]), *wire[1:]],
            [replace(merged, instructions="other instructions"), *wire[1:]],
            [*wire[:2], replace(tool, parts=[ToolReturnPart("read", {"value": 131}, "call")])],
            wire[:-1],
        ):
            with self.subTest(changed=changed), self.assertRaisesRegex(SummaryUnavailable, "source_history_changed"):
                _compaction_source_view(source, changed)
