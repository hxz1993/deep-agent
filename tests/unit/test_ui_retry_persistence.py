"""UI correction exhaustion through the real SDK, runner, and SQLite Event Store."""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent, FunctionToolset, ModelRetry, ToolDenied
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.active_runs import ActiveRunRegistry
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.history import repair_unprocessed_tool_calls
from deep_agent.agent.runner import AgentRunner
from deep_agent.capabilities.ui.builtin import builtin_ui_records
from deep_agent.capabilities.ui.registry import build_external_toolset
from deep_agent.conversation import project_session_events
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class UIRetryPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self.engine, _ = await make_sqlite_stores()
        self.store = stores.coordinator
        self.agent_id = await seed_default_agent(stores, name="isolated-ui-retry")
        self.persister = EventPersister(self.store)
        await self.persister.start()
        self.registry = ActiveRunRegistry()
        self.no_title = patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock)
        self.no_title.start()

    async def asyncTearDown(self) -> None:
        self.no_title.stop()
        self.registry.cancel_all()
        await self.persister.aclose()
        await self.engine.dispose()

    def _runner(self, stream, *, toolset=None) -> AgentRunner:
        agent = Agent(
            FunctionModel(stream_function=stream),
            deps_type=AgentDeps,
            toolsets=[toolset or build_external_toolset(builtin_ui_records())],
        )
        return AgentRunner(
            agent, self.store, UsageLimits(request_limit=10),
            active_runs=self.registry,
            ui_tool_hints={"builtin__confirm": {"widget": "confirm"}},
        )

    async def _collect(self, runner, session):
        deps = AgentDeps(session_id=session, user="isolated-user", agent_id=self.agent_id)
        events = []
        async for event in runner.run_stream(session, "synthetic confirmation", deps, persister=self.persister):
            events.append(event)
        await self.persister.flush(session, agent_id=self.agent_id)
        return events

    async def test_exhausted_ui_retry_is_durable_failed_and_next_turn_runs(self):
        attempts = 0

        async def invalid_stream(messages, info):
            nonlocal attempts
            attempts += 1
            yield f"Correction attempt {attempts}. "
            yield {0: DeltaToolCall(name="builtin__confirm", json_args="{}", tool_call_id=f"bad-{attempts}")}

        session = "synthetic-ui-exhaustion"
        events = await asyncio.wait_for(self._collect(self._runner(invalid_stream), session), 5)
        self.assertEqual(attempts, 4)
        self.assertEqual(events[-1]["type"], "error")
        self.assertTrue(events[-1]["history_persisted"])
        self.assertFalse(any(e["type"] == "tool_call" for e in events))
        self.assertEqual(self.registry.active_count(), 0)
        stored = await self.store.list_events(session, limit=100)
        failures = [e for e in stored if e.event_type == "execution/error"]
        self.assertEqual(len(failures), 1)
        diagnostic = failures[0].payload
        self.assertEqual(diagnostic["code"], "tool_retry_exhausted")
        self.assertEqual(diagnostic["phase"], "tool_validation")
        self.assertIn("builtin__confirm", diagnostic["message"])
        self.assertIn("3", diagnostic["message"])
        history = project_session_events(stored, session_id=session)
        tools = [i for i in history.items if i.kind == "tool"]
        self.assertEqual([(i.status, i.outcome) for i in tools], [("failed", "failed")])
        self.assertFalse(any(i.status in {"running", "pending", "awaiting_input"} for i in history.items))
        first_ids = [e.event_id for e in stored]

        async def recovered_stream(messages, info):
            yield "RECOVERED"

        recovered = await asyncio.wait_for(self._collect(self._runner(recovered_stream), session), 5)
        self.assertEqual(recovered[-1]["type"], "final")
        self.assertEqual(recovered[-1]["status"], "completed")
        self.assertEqual(self.registry.active_count(), 0)
        after = await self.store.list_events(session, limit=100)
        self.assertEqual([e.event_id for e in after[:len(first_ids)]], first_ids)

    async def test_diagnostic_does_not_copy_validation_secrets(self):
        toolset = build_external_toolset(builtin_ui_records())
        secret = "synthetic-secret-do-not-persist"

        def reject(ctx, **args):
            raise ModelRetry(f"Authorization: Bearer {secret}; password={secret}")

        toolset._validators["builtin__confirm"] = reject

        async def invalid_stream(messages, info):
            yield {0: DeltaToolCall(name="builtin__confirm", json_args='{"message":"confirm"}', tool_call_id="reused-id")}

        session = "synthetic-ui-secret"
        await asyncio.wait_for(self._collect(self._runner(invalid_stream, toolset=toolset), session), 5)
        stored = await self.store.list_events(session, limit=100)
        self.assertNotIn(secret, json.dumps([e.public_dict() for e in stored], default=str))
        failures = [e for e in stored if e.event_type == "execution/error"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].payload["code"], "tool_retry_exhausted")
        self.assertEqual(failures[0].payload["phase"], "tool_validation")

    async def test_execution_exception_closes_published_card_without_persisting_secret(self):
        secret = "synthetic-provider-secret-do-not-store"

        async def broken_tool() -> str:
            raise RuntimeError(f"Authorization: Bearer {secret}")

        async def stream(messages, info):
            yield {0: DeltaToolCall(name="broken_tool", json_args="{}", tool_call_id="broken-call")}

        session = "synthetic-tool-exception"
        events = await asyncio.wait_for(self._collect(
            self._runner(stream, toolset=FunctionToolset(tools=[broken_tool])), session,
        ), 5)
        call_index = next(i for i, event in enumerate(events) if event["type"] == "tool_call")
        result_index = next(i for i, event in enumerate(events) if event["type"] == "tool_result")
        self.assertLess(call_index, result_index)
        self.assertLess(result_index, len(events) - 1)
        self.assertEqual(events[result_index]["tool_call_id"], "broken-call")
        self.assertEqual(events[result_index]["outcome"], "failed")
        self.assertNotIn(secret, json.dumps(events[result_index]))
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(self.registry.active_count(), 0)
        stored = await self.store.list_events(session, limit=100)
        self.assertNotIn(secret, json.dumps([e.public_dict() for e in stored], default=str))
        failures = [e for e in stored if e.event_type == "execution/error"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].payload["code"], "agent_run_failed")
        history = project_session_events(stored, session_id=session)
        tool = next(item for item in history.items if item.kind == "tool")
        self.assertEqual((tool.status, tool.outcome), ("failed", "failed"))

    def test_normal_denied_repair_is_unchanged(self):
        repaired = repair_unprocessed_tool_calls([
            ModelResponse(parts=[ToolCallPart("builtin__confirm", {}, "denied-call")])
        ])
        self.assertIsInstance(repaired[-1], ModelRequest)
        self.assertIsInstance(repaired[-1].parts[0].content, ToolDenied)
        self.assertEqual(repaired[-1].parts[0].outcome, "success")
