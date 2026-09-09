from __future__ import annotations

import asyncio
import unittest

import anyio
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.context.compaction import DeepAgentContextCompaction
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.runner import AgentRunner
from deep_agent.db.serialization import serialize_message
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent
from tests.unit.test_context_compaction import valid_summary


class ManualCompactionDisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self.engine, _ = await make_sqlite_stores()
        self.store = stores.coordinator
        self.agent_id = await seed_default_agent(stores)
        self.session_id = "manual-disconnect"
        self.user_id = "isolated-user"
        await self._append_history("initial")
        runner = self._runner(TestModel(call_tools=[], custom_output_text=valid_summary()))
        results = [event async for event in runner.run_manual_compaction(self.session_id, self._deps())]
        self.assertEqual(results[-1]["status"], "completed", results[-1])
        await self._append_history("new")
        self.previous_anchor = await self.store.latest_completed_compaction(self.session_id)
        self.raw_before = await self._serialized_history(raw=True)
        self.visible_before = await self._serialized_history(raw=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    def _deps(self) -> AgentDeps:
        return AgentDeps(session_id=self.session_id, user=self.user_id, agent_id=self.agent_id)

    def _runner(self, model) -> AgentRunner:
        return AgentRunner(
            Agent(model, deps_type=AgentDeps), self.store, UsageLimits(request_limit=10),
            context_window_tokens=128_000,
            max_output_tokens=8192,
            context_compaction=DeepAgentContextCompaction(
                context_window_tokens=128_000, max_output_tokens=8192,
            ),
        )

    async def _append_history(self, label: str) -> None:
        messages = []
        for turn in range(3):
            messages.extend([
                ModelRequest(parts=[UserPromptPart(content=f"{label}-{turn}:" + "背景记录。" * 1000)]),
                ModelResponse(parts=[TextPart(content=f"{label}-{turn}:" + "确定方案。" * 1000)]),
            ])
        await self.store.append(
            self.session_id, messages, agent_id=self.agent_id, user_id=self.user_id,
            turn_id=label, run_id=label,
        )

    async def _serialized_history(self, *, raw: bool):
        reader = self.store.get_raw_history if raw else self.store.get_history
        messages = await reader(self.session_id, agent_id=self.agent_id)
        result = [serialize_message(message) for message in messages]
        if not raw and result:
            # The synthetic anchor is reconstructed on each read; its generated
            # timestamps are not persisted facts. All original messages remain
            # compared byte-for-byte, including their timestamps.
            result[0].pop("timestamp", None)
            for part in result[0].get("parts", []):
                part.pop("timestamp", None)
        return result

    async def _assert_disconnect(self, mode: str) -> None:
        entered = asyncio.Event()
        exited = asyncio.Event()

        async def summary_model(messages, info):
            entered.set()
            try:
                await asyncio.Event().wait()
                return ModelResponse(parts=[TextPart(valid_summary())])
            finally:
                exited.set()

        runner = self._runner(FunctionModel(function=summary_model))
        deps = self._deps()
        stream = runner.run_manual_compaction(self.session_id, deps)
        consumer = None
        try:
            if mode == "close":
                async for event in stream:
                    if event.get("phase") == "summarizing":
                        break
                await asyncio.wait_for(entered.wait(), 3)
                await asyncio.wait_for(stream.aclose(), 3)
            else:
                scopes = []

                async def consume():
                    with anyio.CancelScope() as scope:
                        scopes.append(scope)
                        async for _ in stream:
                            pass

                consumer = asyncio.create_task(consume())
                await asyncio.wait_for(entered.wait(), 3)
                if mode == "scope":
                    scopes[0].cancel()
                    await asyncio.wait_for(consumer, 3)
                else:
                    consumer.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await consumer

            self.assertTrue(exited.is_set(), "summary Provider task did not exit")
            self.assertEqual(await self._serialized_history(raw=True), self.raw_before)
            self.assertEqual(await self._serialized_history(raw=False), self.visible_before)
            anchor = await self.store.latest_completed_compaction(self.session_id)
            self.assertEqual(anchor.public_dict(), self.previous_anchor.public_dict())
            rows = await self.store.list_events(self.session_id, limit=100)
            attempts = [row for row in rows if row.event_type == "compaction"]
            self.assertEqual([row.status for row in attempts], ["completed", "cancelled"])
            self.assertEqual(attempts[-1].run_id, deps.run_id)
            self.assertEqual(attempts[-1].payload["error"]["code"], "cancelled")
        finally:
            if consumer is not None and not consumer.done():
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
            await stream.aclose()

    async def test_generator_close_during_summary_keeps_history_and_anchor(self) -> None:
        await self._assert_disconnect("close")

    async def test_task_cancel_during_summary_keeps_history_and_anchor(self) -> None:
        await self._assert_disconnect("task")

    async def test_sse_cancel_scope_during_summary_keeps_history_and_anchor(self) -> None:
        await self._assert_disconnect("scope")

    async def test_generator_close_before_commit_keeps_history_and_anchor(self) -> None:
        runner = self._runner(TestModel(call_tools=[], custom_output_text=valid_summary()))
        deps = self._deps()
        stream = runner.run_manual_compaction(self.session_id, deps)
        reached_commit = False
        try:
            async for event in stream:
                if event.get("phase") == "committing":
                    reached_commit = True
                    await asyncio.wait_for(stream.aclose(), 3)
                    break
            self.assertTrue(reached_commit, "fixture did not reach the pre-commit boundary")
            self.assertEqual(await self._serialized_history(raw=True), self.raw_before)
            self.assertEqual(await self._serialized_history(raw=False), self.visible_before)
            anchor = await self.store.latest_completed_compaction(self.session_id)
            self.assertEqual(anchor.public_dict(), self.previous_anchor.public_dict())
            rows = await self.store.list_events(self.session_id, limit=100)
            attempts = [row for row in rows if row.event_type == "compaction"]
            self.assertEqual([row.status for row in attempts], ["completed", "cancelled"])
            self.assertEqual(attempts[-1].run_id, deps.run_id)
            self.assertEqual(attempts[-1].payload["error"]["code"], "cancelled")
        finally:
            await stream.aclose()
