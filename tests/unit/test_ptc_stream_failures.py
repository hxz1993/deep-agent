"""Real streamed PTC/runner/persistence boundaries; no external model or MCP."""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent, FunctionToolset, ModelRetry
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.active_runs import ActiveRunRegistry
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.runner import AgentRunner
from deep_agent.ptc import FilePayloadResolvingToolset, HybridCodeMode
from deep_agent.ptc.file_payload import build_code_mode_tool_package
from deep_agent.conversation import project_session_events
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


def _call(name: str, args: dict, call_id: str) -> dict:
    return {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=call_id)}


class PTCStreamFailureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        stores, self.engine, _ = await make_sqlite_stores()
        self.store = stores.coordinator
        self.agent_id = await seed_default_agent(stores, name="isolated-ptc-stream")
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

    def _runner(self, stream, tools=(), native_tools=()) -> AgentRunner:
        agent = Agent(
            FunctionModel(stream_function=stream),
            deps_type=AgentDeps,
            toolsets=[
                build_code_mode_tool_package().toolset,
                FilePayloadResolvingToolset(FunctionToolset(tools=list(tools)).prefixed("demo")),
                FunctionToolset(tools=list(native_tools)),
            ],
            capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True, max_retries=1)],
        )
        return AgentRunner(agent, self.store, UsageLimits(request_limit=12), active_runs=self.registry)

    async def _collect(self, runner: AgentRunner, session: str, events: list[dict]) -> None:
        deps = AgentDeps(session_id=session, user="isolated-user", agent_id=self.agent_id)
        async for event in runner.run_stream(session, "synthetic canvas request", deps, persister=self.persister):
            events.append(event)

    async def _history(self, session: str):
        await self.persister.flush(session, agent_id=self.agent_id)
        stored = await self.store.list_events(session, limit=100)
        return stored, project_session_events(stored, session_id=session)

    async def test_four_ptc_errors_keep_pairing_then_native_fallback_and_final(self) -> None:
        """Four bad snippets must not consume a shared framework retry budget."""
        step = 0
        calls = []

        async def pair() -> dict[str, str]:
            calls.append("pair")
            return {"status": "paired", "connection_id": "synthetic-connection"}

        async def snapshot() -> dict[str, str]:
            calls.append("snapshot")
            return {"status": "ready", "revision": "synthetic-revision"}

        async def search_tools(query: str) -> str:
            calls.append("search")
            return "demo_snapshot"

        async def stream(messages, info):
            nonlocal step
            step += 1
            if step == 1:
                yield _call("run_code", {"code": "await demo_pair()"}, "pair-code")
            elif step <= 5:
                yield _call("run_code", {"code": "await search_tools(query='snapshot')"}, f"bad-{step - 1}")
            elif step == 6:
                yield _call("search_tools", {"query": "snapshot"}, "native-search")
            elif step == 7:
                yield _call("demo_snapshot", {}, "native-snapshot")
            else:
                yield "Recovered using the native snapshot."

        events = []
        runner = self._runner(stream, tools=[pair, snapshot], native_tools=[search_tools])
        await asyncio.wait_for(self._collect(runner, "ptc-recovery", events), timeout=5)
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["status"], "completed")
        self.assertEqual(calls, ["pair", "search", "snapshot"])
        failed = [event for event in events if event.get("type") == "tool_result" and event.get("outcome") == "failed"]
        self.assertEqual(len(failed), 4, [event for event in events if event.get("type") == "tool_result"])
        self.assertTrue(all(event["metadata"]["code_mode"] for event in failed))
        self.assertFalse(any(event.get("type") == "tool_call_removed" for event in events))
        raw = await self.store.get_raw_history("ptc-recovery", agent_id=self.agent_id)
        returns = [part for message in raw for part in message.parts if isinstance(part, ToolReturnPart)]
        self.assertEqual([part.tool_call_id for part in returns], ["pair-code", "bad-1", "bad-2", "bad-3", "bad-4", "native-search", "native-snapshot"])
        self.assertEqual([part.outcome for part in returns[1:5]], ["failed"] * 4)
        self.assertEqual(returns[0].metadata["ptc_diagnostics"]["steps"][0]["status"], "completed")
        _, history = await self._history("ptc-recovery")
        tools = {item.call_id: item for item in history.items if item.kind == "tool"}
        self.assertEqual(tools["pair-code"].status, "completed")
        self.assertTrue(all(tools[f"bad-{i}"].status == "failed" for i in range(1, 5)))
        self.assertEqual(tools["native-snapshot"].status, "completed")
        self.assertFalse(any(item.status in {"running", "pending", "awaiting_input"} for item in history.items))
        self.assertEqual(self.registry.active_count(), 0)

    async def test_cancel_while_nested_ptc_tool_waits_keeps_success_and_closes_pending(self) -> None:
        entered = asyncio.Event()
        exited = asyncio.Event()
        step = 0

        async def pair() -> str:
            return "paired"

        async def wait_for_snapshot() -> str:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                exited.set()
            return "unreachable"

        async def stream(messages, info):
            nonlocal step
            step += 1
            code = "await demo_pair()" if step == 1 else "await demo_wait_for_snapshot()"
            yield _call("run_code", {"code": code}, "pair-code" if step == 1 else "waiting-code")

        events = []
        runner = self._runner(stream, tools=[pair, wait_for_snapshot])
        task = asyncio.create_task(self._collect(runner, "ptc-cancel", events))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            self.assertTrue(self.registry.request_cancel(agent_id=self.agent_id, session_id="ptc-cancel", user_id="isolated-user"))
            await asyncio.wait_for(task, timeout=3)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(exited.is_set(), "nested await leaked after cancellation")
        self.assertEqual(events[-1]["type"], "cancelled")
        self.assertEqual(self.registry.active_count(), 0)
        _, history = await self._history("ptc-cancel")
        tools = {item.call_id: item for item in history.items if item.kind == "tool"}
        self.assertEqual(tools["pair-code"].status, "completed")
        self.assertEqual(tools["waiting-code"].status, "interrupted")
        raw = await self.store.get_raw_history("ptc-cancel", agent_id=self.agent_id)
        waiting_returns = [part for message in raw for part in message.parts
                           if isinstance(part, ToolReturnPart) and part.tool_call_id == "waiting-code"]
        self.assertEqual(len(waiting_returns), 1)
        self.assertEqual(waiting_returns[0].metadata["ptc_diagnostics"]["summary"]["status"], "interrupted")
        self.assertFalse(any(item.status in {"running", "pending", "awaiting_input"} for item in history.items))

    async def test_provider_failure_after_completed_ptc_keeps_prior_tool_result(self) -> None:
        step = 0

        async def pair() -> str:
            return "paired"

        async def stream(messages, info):
            nonlocal step
            step += 1
            if step == 1:
                yield _call("run_code", {"code": "await demo_pair()"}, "pair-code")
            else:
                yield "partial explanation"
                raise RuntimeError("synthetic provider stream failure")

        events = []
        await asyncio.wait_for(self._collect(self._runner(stream, tools=[pair]), "ptc-provider-failure", events), timeout=5)
        self.assertEqual(events[-1]["type"], "error")
        self.assertTrue(events[-1]["history_persisted"])
        self.assertEqual(self.registry.active_count(), 0)
        _, history = await self._history("ptc-provider-failure")
        tools = {item.call_id: item for item in history.items if item.kind == "tool"}
        self.assertEqual(tools["pair-code"].status, "completed")
        self.assertFalse(any(item.status in {"running", "pending", "awaiting_input"} for item in history.items))

    async def test_framework_retry_exhaustion_terminates_and_preserves_prior_ptc(self) -> None:
        """Framework guard failures must not leave the SSE consumer waiting forever."""
        step = 0

        async def pair() -> str:
            return "paired"

        async def rejected_native() -> str:
            raise ModelRetry("synthetic retryable validation")

        async def stream(messages, info):
            nonlocal step
            step += 1
            if step == 1:
                yield _call("run_code", {"code": "await demo_pair()"}, "pair-code")
            else:
                yield _call("rejected_native", {}, f"retry-{step}")

        events = []
        runner = self._runner(stream, tools=[pair], native_tools=[rejected_native])
        await asyncio.wait_for(self._collect(runner, "ptc-framework-failure", events), timeout=5)
        self.assertEqual(events[-1]["type"], "error")
        self.assertTrue(events[-1]["history_persisted"])
        self.assertEqual(self.registry.active_count(), 0)
        _, history = await self._history("ptc-framework-failure")
        tools = {item.call_id: item for item in history.items if item.kind == "tool"}
        self.assertEqual(tools["pair-code"].status, "completed")
        self.assertFalse(any(item.status in {"running", "pending", "awaiting_input"} for item in history.items))
