from __future__ import annotations

import asyncio
import time
import unittest
from dataclasses import dataclass, field
from decimal import Decimal

from pydantic_ai import Agent, FunctionToolset, Tool, ToolDenied, ToolFailed
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from deep_agent.agent.deps import AgentDeps
from deep_agent.ptc.async_runtime import close_runtime, execute_code, reset_runtime
from deep_agent.ptc.capability import HybridCodeMode
from deep_agent.ptc.diagnostics import current_collector
from deep_agent.ptc.file_payload import CODE_MODE_HYBRID, CODE_MODE_METADATA_KEY
from deep_agent.ptc.toolset import HybridCodeModeToolset


@dataclass
class _AsyncToolset(HybridCodeModeToolset):
    deadline: float = 120.0
    executions: list = field(default_factory=list)

    async def call_tool(self, name, tool_args, ctx, tool):
        if name != "run_code":
            return await super().call_tool(name, tool_args, ctx, tool)
        if tool_args.get("restart"):
            await reset_runtime(self)
        async with asyncio.timeout(self.deadline):
            return await super().call_tool(name, tool_args, ctx, tool)

    async def _execute_code(self, code, ctx, tool, execution):
        self.executions.append(execution)
        collector = current_collector.get()
        if collector is not None:
            collector.execution = execution
        return await execute_code(self, code, ctx, tool, execution)

    async def __aexit__(self, *args):
        await close_runtime(self)
        return await super().__aexit__(*args)


@dataclass
class _AsyncCapability(HybridCodeMode):
    instances: list = field(default_factory=list)
    deadline: float = 120.0

    def get_wrapper_toolset(self, wrapped):
        toolset = _AsyncToolset(
            wrapped=wrapped, tool_selector=self.tools, max_retries=self.max_retries,
            max_tool_calls=self.max_tool_calls, resource_limits=self.resource_limits,
            dynamic_catalog=self.dynamic_catalog, os_access=self.os_access, mount=self.mount,
        )
        toolset.executions = []
        toolset.deadline = self.deadline
        self.instances.append(toolset)
        return toolset


def _parts(result):
    return [part for message in result.all_messages() for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "run_code"]


class AsyncCodeModeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def _agent(self, function, calls, *, deadline=120.0, sequential=False, requires_approval=False):
        def respond(messages, info):
            step = sum(isinstance(message, ModelResponse) for message in messages)
            if step < len(calls):
                args = calls[step]
                if isinstance(args, str):
                    args = {"code": args}
                return ModelResponse(parts=[ToolCallPart("run_code", args, f"outer-{step}")])
            return ModelResponse(parts=[TextPart("done")])

        capability = _AsyncCapability(
            tools=[], dynamic_catalog=True, max_tool_calls=100, deadline=deadline,
            resource_limits={"max_duration_secs": 2, "max_memory": 256 * 1024 * 1024},
        )
        tool = Tool(function, name="data", sequential=sequential, requires_approval=requires_approval,
                    metadata={CODE_MODE_METADATA_KEY: CODE_MODE_HYBRID})
        agent = Agent(FunctionModel(respond), deps_type=AgentDeps,
                      toolsets=[FunctionToolset(tools=[tool])], capabilities=[capability])
        return agent, AgentDeps(session_id="isolated-async-ptc"), capability

    async def test_deadline_interrupts_cpu_after_external_wait_and_keeps_loop_responsive(self):
        calls = []

        async def data(value: int = 1) -> int:
            calls.append(value)
            await asyncio.sleep(0.06)
            return value

        agent, deps, _ = self._agent(data, ["value = await data()\nwhile True:\n    pass"], deadline=0.18)
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        ticking = asyncio.create_task(ticker())
        start = time.monotonic()
        try:
            with self.assertRaises(TimeoutError):
                await agent.run("CPU deadline", deps=deps)
        finally:
            ticking.cancel()
            await asyncio.gather(ticking, return_exceptions=True)
        self.assertLess(time.monotonic() - start, 1.0)
        self.assertGreater(ticks, 8)
        self.assertEqual(calls, [1])
        self.assertIsNone(current_collector.get())

    async def test_cancel_joins_async_host_callbacks_and_a_later_run_succeeds(self):
        entered = asyncio.Event()
        cleaned = asyncio.Event()
        running = set()
        should_wait = True

        async def data() -> int:
            task = asyncio.current_task()
            running.add(task)
            try:
                if should_wait:
                    entered.set()
                    await asyncio.sleep(10)
                return 7
            finally:
                running.discard(task)
                cleaned.set()

        agent, deps, capability = self._agent(data, ["await data()"])
        pending = asyncio.create_task(agent.run("cancel", deps=deps))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        self.assertTrue(cleaned.is_set())
        self.assertFalse(running)
        should_wait = False
        result = await agent.run("later independent run", deps=deps)
        self.assertEqual(_parts(result)[0].content, 7)
        self.assertTrue(capability.instances)

    async def test_host_task_cancellation_survives_monty_exception_transport(self):
        entered = asyncio.Event()
        host_task = None

        async def data() -> int:
            nonlocal host_task
            host_task = asyncio.current_task()
            entered.set()
            await asyncio.Event().wait()
            return 1

        agent, deps, _ = self._agent(data, ["await data()"])
        pending = asyncio.create_task(agent.run("cancel the host callback", deps=deps))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            self.assertIsNotNone(host_task)
            host_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 3)
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        self.assertTrue(host_task.done())
        self.assertIsNone(current_collector.get())

    async def test_same_run_variables_survive_and_restart_discards_them(self):
        async def data(value: int = 2) -> int:
            return value

        agent, deps, _ = self._agent(data, [
            "remembered = await data(value=41)\nremembered",
            "remembered + 1",
            {"code": "remembered", "restart": True},
            "fresh = await data(value=7)\nfresh",
        ])
        result = await agent.run("REPL", deps=deps)
        parts = _parts(result)
        self.assertEqual(parts[0].content, 41)
        self.assertEqual(parts[1].content, 42)
        self.assertEqual(parts[2].outcome, "failed")
        self.assertEqual(parts[3].content, 7)
        second = await agent.run("independent run", deps=deps)
        self.assertEqual(_parts(second)[0].content, 41)

    async def test_100_calls_succeed_101st_is_refused_and_completed_facts_survive(self):
        called = []

        async def data(value: int) -> int:
            called.append(value)
            return value

        agent, deps, capability = self._agent(data, [
            "values = [await data(value=i) for i in range(100)]\nlen(values)",
            "values = [await data(value=i) for i in range(101)]\nlen(values)",
        ])
        result = await agent.run("budget", deps=deps)
        parts = _parts(result)
        self.assertEqual(parts[0].content, 100)
        self.assertEqual(parts[1].outcome, "failed")
        self.assertEqual(called, list(range(100)) * 2)
        executions = [execution for item in capability.instances for execution in item.executions]
        self.assertEqual([len(item.nested_calls) for item in executions], [100, 100])
        self.assertEqual([len(item.nested_returns) for item in executions], [100, 100])

    async def test_failed_and_denied_tools_are_not_successful_nested_returns(self):
        async def data(value: int) -> object:
            if value == 1:
                raise ToolFailed("synthetic expected failure")
            if value == 2:
                return ToolDenied("synthetic denied")
            return {"value": value}

        agent, deps, capability = self._agent(data, [
            "await data(value=1)", "await data(value=2)", "await data(value=3)",
        ])
        result = await agent.run("failures", deps=deps)
        parts = _parts(result)
        self.assertEqual([part.outcome for part in parts], ["failed", "failed", "success"])
        executions = [execution for item in capability.instances for execution in item.executions]
        outcomes = [next(iter(item.nested_returns.values())).outcome for item in executions]
        self.assertEqual(outcomes, ["failed", "denied", "success"])

    async def test_approval_required_tool_cannot_be_treated_as_completed_data(self):
        called = []

        async def data() -> int:
            called.append(True)
            return 7

        agent, deps, capability = self._agent(data, ["await data()"], requires_approval=True)
        result = await agent.run("approval is native only", deps=deps)
        self.assertEqual(_parts(result)[0].outcome, "failed")
        self.assertFalse(called)
        execution = next(item for view in capability.instances for item in view.executions)
        self.assertEqual(next(iter(execution.nested_returns.values())).outcome, "failed")

    async def test_sequential_tool_uses_sync_stub_without_blocking_the_main_loop(self):
        async def data(value: int) -> int:
            await asyncio.sleep(0.02)
            self.assertIsNotNone(current_collector.get())
            return value

        agent, deps, _ = self._agent(data, ["data(value=7) + 1"], sequential=True)
        result = await agent.run("sync stub", deps=deps)
        self.assertEqual(_parts(result)[0].content, 8)

    async def test_gather_failure_joins_the_other_started_host_callback(self):
        slow_started = asyncio.Event()
        slow_cleaned = asyncio.Event()
        running = set()

        async def data(value: int) -> int:
            task = asyncio.current_task()
            running.add(task)
            try:
                if value == 1:
                    slow_started.set()
                    try:
                        await asyncio.sleep(10)
                    finally:
                        slow_cleaned.set()
                else:
                    await slow_started.wait()
                    raise ToolFailed("synthetic gather failure")
                return value
            finally:
                running.discard(task)

        agent, deps, capability = self._agent(data, [
            "import asyncio\nawait asyncio.gather(data(value=1), data(value=2))",
        ])
        result = await agent.run("parallel failure", deps=deps)
        self.assertEqual(_parts(result)[0].outcome, "failed")
        self.assertTrue(slow_started.is_set())
        self.assertTrue(slow_cleaned.is_set())
        self.assertFalse(running)
        execution = next(item for view in capability.instances for item in view.executions)
        self.assertEqual(len(execution.nested_calls), 2)
        self.assertEqual(sorted(part.outcome for part in execution.nested_returns.values()),
                         ["failed", "interrupted"])

    async def test_sequential_callback_is_joined_on_cancel(self):
        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def data() -> int:
            started.set()
            try:
                await asyncio.sleep(10)
            finally:
                cleaned.set()
            return 7

        agent, deps, _ = self._agent(data, ["data()"], sequential=True)
        pending = asyncio.create_task(agent.run("cancel sequential", deps=deps))
        try:
            await asyncio.wait_for(started.wait(), 3)
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        self.assertTrue(cleaned.is_set())

    async def test_regular_runtime_failure_preserves_prior_assignments(self):
        async def data() -> int:
            return 41

        agent, deps, _ = self._agent(data, [
            "remembered = await data()\nremembered", "1 / 0", "remembered + 1",
        ])
        result = await agent.run("keep earlier state", deps=deps)
        parts = _parts(result)
        self.assertEqual(parts[0].content, 41)
        self.assertEqual(parts[1].outcome, "failed")
        self.assertEqual(parts[2].content, 42)

    async def test_sdk_binary_and_json_leaf_conversions_are_preserved(self):
        async def data() -> dict:
            return {"bytes": b"\xff\x00\x01", "decimal": Decimal("1.25")}

        agent, deps, _ = self._agent(data, [
            "value = await data()\n{'size': len(value['bytes']), 'decimal': value['decimal']}",
        ])
        result = await agent.run("native binary is not utf8 text", deps=deps)
        self.assertEqual(_parts(result)[0].content, {"size": 3, "decimal": "1.25"})

    async def test_parallel_call_reservation_rejects_101_before_unbounded_host_tasks(self):
        calls = []

        async def data(value: int) -> int:
            calls.append(value)
            await asyncio.sleep(0.001)
            return value

        agent, deps, capability = self._agent(data, [
            "import asyncio\nawait asyncio.gather(*[data(value=i) for i in range(101)])",
        ])
        result = await agent.run("parallel budget", deps=deps)
        self.assertEqual(_parts(result)[0].outcome, "failed")
        self.assertLessEqual(len(calls), 100)
        execution = next(item for view in capability.instances for item in view.executions)
        self.assertEqual(execution.call_count, 100)
        self.assertTrue(execution.budget_exhausted)


if __name__ == "__main__":
    unittest.main()
