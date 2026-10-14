from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from pydantic_ai import Agent, DeferredToolResults
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deferred import DeferredResultConflict, validate_deferred_results
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.runner import AgentRunner
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class DeferredAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, _ = await make_sqlite_stores()
        self.store = self.stores.coordinator
        self.agent_id = await seed_default_agent(self.stores)
        self.persister = EventPersister(self.store)
        await self.persister.start()
        self.requests = 0

        async def respond(messages, info):
            self.requests += 1
            return ModelResponse(parts=[TextPart("finished")])

        async def stream(messages, info):
            await respond(messages, info)
            yield "finished"

        self.runner = AgentRunner(
            Agent(FunctionModel(respond, stream_function=stream), deps_type=AgentDeps),
            self.store, UsageLimits(request_limit=5),
        )

    async def asyncTearDown(self):
        await self.persister.aclose()
        await self.engine.dispose()

    async def append(self, messages, status=None, run="original"):
        await self.store.append("fixture", messages, agent_id=self.agent_id,
                                user_id="fixture-user", run_id=run, turn_id=run,
                                terminal_status=status)

    async def pending(self, call="ui", run="original"):
        await self.append([ModelResponse(parts=[ToolCallPart("confirm", {}, call)])], "pending", run)

    async def snapshot(self):
        return [row.public_dict() for row in await self.store.list_all_events("fixture")]

    async def run_resume(self, calls, streaming=True):
        deps = AgentDeps(session_id="fixture", agent_id=self.agent_id, user="fixture-user")
        with patch.object(AgentRunner, "_generate_model_title", return_value=None):
            if streaming:
                events = [e async for e in self.runner.run_stream(
                    "fixture", "", deps, deferred_tool_results=DeferredToolResults(calls=calls),
                    persister=self.persister)]
                await self.persister.flush("fixture", agent_id=self.agent_id)
                return events[-1]["status"]
            return (await self.runner.run(
                "fixture", "", deps, deferred_tool_results=DeferredToolResults(calls=calls)))["status"]

    async def test_completed_duplicate_changed_result_and_sync_do_not_append_errors(self):
        await self.pending()
        self.assertEqual(await self.run_resume({"ui": {"confirmed": True}}), "completed")
        before = await self.snapshot()
        for streaming in (True, False):
            for answer in (True, False):
                with self.assertRaises(DeferredResultConflict):
                    await self.run_resume({"ui": {"confirmed": answer}}, streaming)
                self.assertEqual(await self.snapshot(), before)
        self.assertEqual(self.requests, 1)

    async def test_new_pending_survives_old_mixed_or_unknown_results(self):
        await self.pending("old")
        await self.append([ModelRequest(parts=[ToolReturnPart("confirm", True, "old")]),
                           ModelResponse(parts=[TextPart("done")])], "completed", "resume")
        await self.pending("new", "new")
        before = await self.snapshot()
        for calls in ({"old": True}, {"unknown": True}, {"new": True, "old": True}):
            with self.assertRaises(DeferredResultConflict):
                await self.run_resume(calls)
            self.assertEqual(await self.snapshot(), before)
        self.assertEqual(await self.run_resume({"new": True}), "completed")

    async def test_reused_call_id_in_later_response_is_not_globally_deduplicated(self):
        await self.pending()
        await self.run_resume({"ui": True})
        await self.pending(run="later")
        self.assertEqual(await self.run_resume({"ui": False}), "completed")
        self.assertEqual(self.requests, 2)

    async def test_cancelled_or_abandoned_pending_is_rejected(self):
        for status in ("cancelled", "failed", "interrupted", "completed"):
            await self.append([ModelResponse(parts=[ToolCallPart("confirm", {}, "ui")])],
                              status, status)
            before = await self.snapshot()
            with self.assertRaises(DeferredResultConflict):
                await self.run_resume({"ui": True})
            self.assertEqual(await self.snapshot(), before)
        await self.pending(run="pending-again")
        await self.append([ModelRequest(parts=[UserPromptPart("a new question")])], run="new-user")
        before = await self.snapshot()
        with self.assertRaises(DeferredResultConflict):
            await self.run_resume({"ui": True})
        self.assertEqual(await self.snapshot(), before)
        self.assertEqual(self.requests, 0)

    async def test_concurrent_resumes_that_both_pass_preflight_execute_only_once(self):
        await self.pending()
        barrier = asyncio.Event()
        admitted = 0

        async def check(*args):
            nonlocal admitted
            await validate_deferred_results(*args)
            admitted += 1
            if admitted <= 2:
                if admitted == 2:
                    barrier.set()
                await barrier.wait()

        with patch("deep_agent.agent.runner.validate_deferred_results", side_effect=check):
            outcomes = await asyncio.wait_for(asyncio.gather(
                self.run_resume({"ui": True}), self.run_resume({"ui": True}),
                return_exceptions=True), timeout=10)
        self.assertEqual(sum(x == "completed" for x in outcomes), 1)
        self.assertEqual(sum(isinstance(x, DeferredResultConflict) for x in outcomes), 1)
        self.assertEqual(self.requests, 1)
        rows = await self.snapshot()
        self.assertFalse(any(x["event_type"] == "execution/error" for x in rows))
        results = [p for x in rows for p in x["payload"].get("message", {}).get("parts", [])
                   if p.get("part_kind") == "tool-return" and p.get("tool_call_id") == "ui"]
        self.assertEqual(len(results), 1)
