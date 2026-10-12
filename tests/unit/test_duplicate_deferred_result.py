from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import (
    ModelRequest, ModelResponse, SystemPromptPart, TextPart,
    ToolCallPart, ToolReturnPart, UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.history import deduplicate_adjacent_tool_returns
from deep_agent.agent.message_persistence import StreamMessagePersistenceCapability
from deep_agent.agent.runner import AgentRunner
from deep_agent.capabilities.ui.builtin import build_ui_toolset
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class DuplicateReturnProjectionTests(unittest.TestCase):
    def test_only_exact_local_results_are_removed_without_mutating_facts(self):
        first = ToolReturnPart("lookup", {"value": 1}, "reused", metadata={"safe": True})
        repeated = replace(first, timestamp=first.timestamp + timedelta(seconds=1))
        conflict = replace(first, content={"value": 2})
        history = [
            ModelResponse(parts=[ToolCallPart("lookup", {}, "reused")]),
            ModelRequest(parts=[first, SystemPromptPart("schema available")]),
            ModelRequest(parts=[repeated, conflict]),
            ModelRequest(parts=[UserPromptPart("new turn"), deepcopy(first)]),
            ModelResponse(parts=[ToolCallPart("lookup", {}, "reused")]),
            ModelRequest(parts=[deepcopy(first)]),
        ]
        snapshot = deepcopy(history)
        projected = deduplicate_adjacent_tool_returns(history)
        self.assertEqual(history, snapshot)
        self.assertEqual(projected[2].parts, [conflict])
        self.assertEqual(projected[3].parts, history[3].parts)
        self.assertEqual(projected[5].parts, history[5].parts)

    def test_name_outcome_metadata_and_conflicts_are_not_deduplicated(self):
        original = ToolReturnPart("lookup", "one", "same")
        variants = [replace(original, tool_name="other"),
                    replace(original, outcome="denied"),
                    replace(original, metadata={"different": True}),
                    replace(original, content="two")]
        history = [ModelRequest(parts=[original]), ModelRequest(parts=variants)]
        self.assertIs(deduplicate_adjacent_tool_returns(history), history)

    def test_instruction_only_request_survives_duplicate_removal(self):
        result = ToolReturnPart("lookup", "one", "same")
        history = [ModelRequest(parts=[result]),
                   ModelRequest(parts=[deepcopy(result)], instructions="keep this instruction")]
        projected = deduplicate_adjacent_tool_returns(history)
        self.assertEqual(len(projected), 2)
        self.assertEqual(projected[-1].parts, [])
        self.assertEqual(projected[-1].instructions, "keep this instruction")


class DuplicateDeferredRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, _ = await make_sqlite_stores()
        self.agent_id = await seed_default_agent(self.stores)
        self.persister = EventPersister(self.stores.coordinator)
        await self.persister.start()

    async def asyncTearDown(self):
        await self.persister.aclose()
        await self.engine.dispose()

    def history(self, *, conflict=False):
        result = ToolReturnPart("fixture_lookup", {"value": 7}, "ordinary")
        return [
            ModelRequest(parts=[UserPromptPart("fixture question")]),
            ModelResponse(parts=[TextPart("please confirm"),
                ToolCallPart("fixture_lookup", {}, "ordinary"),
                ToolCallPart("builtin__confirm", {"message": "confirm"}, "ui")]),
            ModelRequest(parts=[result, SystemPromptPart("new schema available")]),
            ModelRequest(parts=[replace(result, content={"value": 8}) if conflict else deepcopy(result)]),
        ]

    async def test_unmodified_sdk_rejects_duplicate_ordinary_return_before_ui_resume(self):
        async def respond(messages, info):
            self.fail("the provider must not be called before duplicate validation")
        agent = Agent(FunctionModel(respond), output_type=[str, DeferredToolRequests],
                      toolsets=[build_ui_toolset()])
        with self.assertRaisesRegex(UserError, "already executed"):
            await agent.run(message_history=self.history(),
                deferred_tool_results=DeferredToolResults(calls={"ui": {"confirmed": True}}))

    async def test_existing_pending_history_resumes_in_both_runner_paths(self):
        for streaming in (False, True):
            session = f"legacy-{streaming}"
            history = self.history()
            await self.stores.coordinator.append(
                session, history, agent_id=self.agent_id, user_id="fixture-user",
                run_id=f"old-{streaming}", turn_id=f"old-{streaming}", terminal_status="pending",
            )
            before = await self.stores.coordinator.get_raw_history(session, agent_id=self.agent_id)
            requests = []
            async def respond(messages, info):
                requests.append(messages)
                return ModelResponse(parts=[TextPart("continued")])
            async def stream(messages, info):
                await respond(messages, info)
                yield "continued"
            runner = AgentRunner(Agent(FunctionModel(respond, stream_function=stream), deps_type=AgentDeps,
                toolsets=[build_ui_toolset()]), self.stores.coordinator, UsageLimits(request_limit=80))
            deps = AgentDeps(session_id=session, agent_id=self.agent_id, user="fixture-user")
            deferred = DeferredToolResults(calls={"ui": {"confirmed": True}})
            with patch.object(AgentRunner, "_generate_model_title", return_value=None):
                if streaming:
                    events = [event async for event in runner.run_stream(
                        session, "", deps, deferred_tool_results=deferred, persister=self.persister)]
                    await self.persister.flush(session, agent_id=self.agent_id)
                    self.assertEqual(events[-1]["status"], "completed")
                else:
                    result = await runner.run(session, "", deps, deferred_tool_results=deferred)
                    self.assertEqual(result["status"], "completed")
            self.assertEqual(len(requests), 1)
            ordinary = [part for message in requests[0] for part in message.parts
                        if isinstance(part, ToolReturnPart) and part.tool_call_id == "ordinary"]
            self.assertEqual(len(ordinary), 1)
            after = await self.stores.coordinator.get_raw_history(session, agent_id=self.agent_id)
            self.assertEqual(after[:len(before)], before)

    async def test_canonical_pending_request_preserves_committed_result_positions(self):
        session = "pending-assembly"
        deps = AgentDeps(session_id=session, agent_id=self.agent_id, user="fixture-user",
                         run_id="pending-run", turn_id="pending-run")
        cap = StreamMessagePersistenceCapability(self.stores.coordinator,
                                                  history_length=0, skip_user_prompt=True)
        response = self.history()[1]
        result = self.history()[2].parts[0]
        cap._ledger = [response]
        cap._accepted = {(id(response), "ordinary"), (id(response), "ui")}
        cap._results = {"ordinary": result}
        # This is the public SDK's pending request shape after draining a queued
        # discovery notice: the ordinary result was already saved incrementally.
        draft = cap._completed_messages()
        await self.stores.coordinator.append_message_parts(
            session, messages=draft, agent_id=self.agent_id, user_id=deps.user,
            run_id=deps.run_id, turn_id=deps.turn_id, first_message_ordinal=1)
        canonical = cap.canonical_messages([
            response,
            ModelRequest(parts=[SystemPromptPart("new schema available")],
                         timestamp=datetime.now(timezone.utc), run_id=deps.run_id),
            ModelRequest(parts=[deepcopy(result)],
                         timestamp=datetime.now(timezone.utc), run_id=deps.run_id),
        ])
        await self.stores.coordinator.append_message_parts(
            session, messages=canonical, agent_id=self.agent_id, user_id=deps.user,
            run_id=deps.run_id, turn_id=deps.turn_id, first_message_ordinal=1,
            terminal_status="pending")
        restored = await self.stores.coordinator.get_raw_history(session, agent_id=self.agent_id)
        returns = [(message, part) for message in restored for part in message.parts
                   if isinstance(part, ToolReturnPart)]
        self.assertEqual(len(returns), 1)
        self.assertEqual(restored[-1].parts[0], result)
        self.assertIsInstance(restored[-1].parts[1], SystemPromptPart)
        rows = await self.stores.coordinator.list_events(session, limit=100)
        stored_returns = [row for row in rows if row.event_type == "tool/results"]
        self.assertEqual(len(stored_returns), 1)
        self.assertEqual(stored_returns[0].payload["source"]["message_ordinal"], 2)
        self.assertEqual(stored_returns[0].payload["source"]["part_index"], 0)
