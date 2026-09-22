from __future__ import annotations

import copy
import unittest

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ThinkingPart, ToolCallPart, ToolReturnPart, UserPromptPart
from sqlalchemy import select

from deep_agent.db.models import ChatSessionEvent
from deep_agent.conversation.projector import project_session_events
from tests.unit import test_session_event_store as fixture


class MessagePartStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.SessionEventStoreTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def append_parts(self, messages, **kwargs):
        return await self.store.append_message_parts(
            "session", agent_id=self.agent_id, user_id="user-1",
            turn_id="turn", run_id="run", messages=messages, **kwargs)

    async def test_parts_append_without_mutating_prior_content_and_reassemble(self):
        response = ModelResponse(parts=[ThinkingPart("thinking"), TextPart("working")])
        first = await self.append_parts([response])
        before = copy.deepcopy(first[0].payload)
        response.parts.append(ToolCallPart("read", {"x": 1}, "call"))
        second = await self.append_parts([response])
        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 1)
        events = await self.store.list_all_events("session")
        self.assertEqual(events[0].payload, before)
        self.assertTrue(all(e.event_type == "assistant/message" for e in events))
        restored = await self.store.get_history("session", agent_id=self.agent_id)
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].parts, response.parts)
        items = project_session_events(events).items
        self.assertEqual([i.kind for i in items], ["assistant_message", "tool"])
        self.assertEqual(items[0].text, "working")

    async def test_parallel_results_are_committed_individually_and_deduplicate_by_call_id(self):
        response = ModelResponse(parts=[
            ToolCallPart("read", {}, "a"), ToolCallPart("read", {}, "b")])
        a = ToolReturnPart("read", "first", "a")
        b = ToolReturnPart("read", "second", "b")
        request = ModelRequest(parts=[b])
        await self.append_parts([response, request])
        partial = await self.store.get_history("session", agent_id=self.agent_id)
        self.assertEqual([p.tool_call_id for p in partial[1].parts], ["b"])
        request.parts = [a, b]
        changed = await self.append_parts([response, request], terminal_status="completed")
        self.assertEqual(len([r for r in changed if r.event_type == "tool/results"]), 2)
        events = await self.store.list_all_events("session")
        self.assertEqual(len(events), 4)
        restored = await self.store.get_history("session", agent_id=self.agent_id)
        self.assertEqual(restored[1].parts, request.parts)
        self.assertEqual(await self.append_parts([response, request], terminal_status="completed"), [])
        self.assertEqual(await self.store.events.find_call_origin("session", ["a", "b"]), ("turn", "run"))

    async def test_stable_ordinals_and_filtered_part_indices_are_not_persisted_as_metadata(self):
        response = ModelResponse(parts=[TextPart("done"), ToolCallPart("read", {}, "a")],
            metadata={"deepagent_message_ordinal": 7, "deepagent_part_indices": [2, 5]})
        await self.append_parts([response])
        events = await self.store.list_all_events("session")
        self.assertEqual([e.payload["source"]["part_index"] for e in events], [2, 5])
        self.assertTrue(all(e.payload["source"]["message_ordinal"] == 7 for e in events))
        self.assertTrue(all(not e.payload["message"].get("metadata") for e in events))
        self.assertEqual((await self.store.get_history("session", agent_id=self.agent_id))[0].metadata, None)

    async def test_prepersisted_user_does_not_duplicate(self):
        user = ModelRequest(parts=[UserPromptPart("hi")])
        await self.store.append("session", [user], agent_id=self.agent_id,
            user_id="user-1", turn_id="turn", run_id="run")
        user.metadata = {"deepagent_message_ordinal": 0, "deepagent_part_indices": [0]}
        await self.append_parts([user, ModelResponse(parts=[TextPart("hello")])])
        self.assertEqual([e.event_type for e in await self.store.list_all_events("session")],
            ["user/message", "assistant/message"])

    async def test_canonical_append_uses_existing_part_identity(self):
        response = ModelResponse(parts=[TextPart("hello"), ToolCallPart("read", {}, "a")])
        await self.append_parts([response])
        await self.store.append("session", [response], agent_id=self.agent_id, user_id="user-1",
            turn_id="turn", run_id="run", first_message_ordinal=1, terminal_status="completed")
        self.assertEqual(len(await self.store.list_all_events("session")), 2)
        from deep_agent.db.session_list import SessionListFilters
        summaries, total = await self.store.list_session_summaries(SessionListFilters(
            agent_id=self.agent_id, user_id="user-1"))
        self.assertEqual(total, 1)
        self.assertEqual(summaries[0].message_count, 1)

    async def test_immutable_content_conflict_rolls_back(self):
        response = ModelResponse(parts=[TextPart("hello")])
        await self.append_parts([response])
        response.parts[0].content = "changed"
        with self.assertRaisesRegex(ValueError, "Immutable message part conflict"):
            await self.append_parts([response])
        self.assertEqual((await self.store.get_history("session", agent_id=self.agent_id))[0].parts[0].content, "hello")

    async def test_empty_terminal_closes_saved_output_without_receipt(self):
        await self.append_parts([ModelResponse(parts=[TextPart("done")])])
        changed = await self.append_parts([], terminal_status="failed")
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].status, "failed")
        self.assertEqual(len(await self.store.list_all_events("session")), 1)

    async def test_ui_call_is_read_only_until_pending_commit(self):
        response = ModelResponse(parts=[ToolCallPart("builtin__confirm", {}, "a")])
        await self.append_parts([response])
        item = project_session_events(await self.store.list_all_events("session")).items[0]
        self.assertEqual(item.status, "pending")
        self.assertIsNone(item.widget)
        await self.append_parts([response], terminal_status="pending")
        item = project_session_events(await self.store.list_all_events("session")).items[0]
        self.assertEqual(item.status, "awaiting_input")
        self.assertEqual(item.mode, "deferred")

    async def test_execution_error_is_bounded_safe_idempotent_and_not_model_history(self):
        await self.append_parts([ModelResponse(parts=[TextPart("partial")])])
        kwargs = dict(agent_id=self.agent_id, user_id="user-1", turn_id="turn", run_id="run", status="failed")
        error = await self.store.append_execution_error("session", **kwargs,
            payload={"code": "provider_failed", "message": 'upstream token="secret-value" Bearer abcdefghi',
                     "phase": "model", "request": {"private": "never"}})
        self.assertEqual(set(error.payload), {"code", "message", "phase"})
        self.assertNotIn("secret-value", str(error.payload))
        self.assertNotIn("abcdefghi", str(error.payload))
        duplicate = await self.store.append_execution_error("session", **kwargs, payload={"code": "late"})
        self.assertEqual(error.event_id, duplicate.event_id)
        self.assertEqual(len(await self.store.get_history("session", agent_id=self.agent_id)), 1)
        with self.assertRaises(ValueError):
            await self.store.append_execution_error("session", **{**kwargs, "status": "cancelled"}, payload={})

    async def test_legacy_run_rows_are_untouched_and_not_restored(self):
        await self.append_parts([ModelResponse(parts=[TextPart("real")])])
        async with self.factory() as db:
            row = (await db.execute(select(ChatSessionEvent))).scalar_one()
            row.event_type = "run/progress"
            row.payload = {"messages": [{"not": "real content"}]}
            await db.commit()
        events = await self.store.list_all_events("session")
        self.assertEqual(events[0].payload, {"messages": [{"not": "real content"}]})
        self.assertEqual(await self.store.get_history("session", agent_id=self.agent_id), [])
        self.assertEqual(project_session_events(events).items, [])

    async def test_compaction_counts_grouped_messages_not_part_rows(self):
        raw = [ModelResponse(parts=[ThinkingPart("old thought"), TextPart("old answer")]),
               ModelRequest(parts=[UserPromptPart("new question")]),
               ModelResponse(parts=[TextPart("new answer")])]
        await self.append_parts(raw)
        snapshot = await self.store.persist_compaction_projection(
            "session", agent_id=self.agent_id, user_id="user-1",
            raw_history=raw,
            projected_history=[ModelRequest(parts=[UserPromptPart("Summary of previous conversation:\n\nsummary")]), *raw[1:]],
            summary_markdown="summary", trigger="manual", model_id="test",
            estimated_before=100, estimated_after=50, target_tokens=60,
            context_window_tokens=1000, preserved_tail_tokens=25, duration_ms=1,
        )
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.payload["source"]["through_sequence"], 2)
        restored = await self.store.get_history("session", agent_id=self.agent_id)
        self.assertEqual(restored[1:], raw[1:])
        self.assertEqual(await self.store.get_raw_history("session", agent_id=self.agent_id), raw)

    async def test_response_with_native_tool_parts_preserves_final_segment(self):
        from pydantic_ai.messages import NativeToolCallPart, NativeToolReturnPart
        raw = [ModelResponse(parts=[TextPart("before"),
               NativeToolCallPart("web_search", {}, "native"),
               NativeToolReturnPart("web_search", "found", "native"), TextPart("final")])]
        await self.append_parts(raw, terminal_status="completed")
        history = project_session_events(await self.store.list_all_events("session"))
        assistants = [item for item in history.items if item.kind == "assistant_message"]
        self.assertEqual([(item.text, item.phase) for item in assistants],
                         [("before", "process"), ("final", "final")])
        self.assertEqual(len(await self.store.get_history("session", agent_id=self.agent_id)), 1)


if __name__ == "__main__":
    unittest.main()
