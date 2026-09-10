"""Deferred result after a user message: SQLite reload -> runner -> model input."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.runner import AgentRunner
from deep_agent.diagnostics.schema import EVENTS, safe_fields
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class HistoryResumePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_reopened_history_sends_one_result_and_preserves_original_events(self):
        stores, engine, _ = await make_sqlite_stores()
        store = stores.coordinator
        agent_id = await seed_default_agent(stores)
        session = "synthetic-late-result"
        persister = EventPersister(store)
        await persister.start()
        try:
            for run, parts in [
                ("original", [ModelResponse(parts=[ToolCallPart("ui", {}, "late-call")])]),
                ("resume", [ModelRequest(parts=[UserPromptPart("fixture user input")]),
                            ModelRequest(parts=[ToolReturnPart("ui", {"ok": True}, "late-call")]),
                            ModelResponse(parts=[TextPart("previous answer")])]),
            ]:
                await store.append(session, parts, agent_id=agent_id, user_id="fixture-user",
                                   turn_id=run, run_id=run)
            before = await store.list_events(session, limit=100)
            observations = []

            async def stream(messages, info):
                results = [p for m in messages for p in m.parts
                           if isinstance(p, ToolReturnPart) and p.tool_call_id == "late-call"]
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0].content, {"ok": True})
                observations.append(len(results))
                yield "Recovered"

            runner = AgentRunner(Agent(FunctionModel(stream_function=stream), deps_type=AgentDeps),
                                 store, UsageLimits(request_limit=3))
            # Simulate another reopen/send, including an unavailable diagnostic sink.
            for broken in (False, True):
                with patch("deep_agent.agent.observability.log_event",
                           side_effect=OSError("unavailable") if broken else None) as log:
                    events = [e async for e in runner.run_stream(
                        session, "new question", AgentDeps(session_id=session, agent_id=agent_id,
                                                           user="fixture-user"), persister=persister)]
                await persister.flush(session, agent_id=agent_id)
                self.assertEqual(events[-1]["type"], "final")
                matched = [c for c in log.call_args_list if c.args[0] == "history_tool_result_matched"]
                self.assertEqual(len(matched), 1)
                fields = matched[0].kwargs
                self.assertEqual(fields["session_id"], session)
                self.assertTrue(fields["run_id"])
                self.assertEqual(fields["tool_call_id"], "late-call")
                self.assertEqual(fields["phase"], "history_prepare")
                self.assertIn("history_tool_result_matched", EVENTS)
                record = safe_fields(fields)
                self.assertEqual(record["reason"], "result_after_user_message")
            self.assertEqual(observations, [1, 1])
            after = await store.list_events(session, limit=100)
            self.assertEqual([e.public_dict() for e in before],
                             [e.public_dict() for e in after[:len(before)]])
        finally:
            await persister.aclose()
            await engine.dispose()
