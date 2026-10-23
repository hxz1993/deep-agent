"""Exercise the actual Monty/ToolManager boundary, not a mocked spill selector."""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent, FunctionToolset, RunContext, ToolReturn
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_output_limits import build_tool_output_limits
from deep_agent.ptc import FilePayloadResolvingToolset, HybridCodeMode
from deep_agent.ptc.file_payload import build_code_mode_tool_package


class PTCProgramByteLimitTests(unittest.TestCase):
    def test_compact_utf8_and_json_escaping_exact_boundary(self):
        from deep_agent.ptc.limits import check_result_size
        from pydantic_ai import ToolFailed
        value = {"文本": ["财务\n\"\\", False, None, 0, .5]}
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
        with patch("deep_agent.ptc.limits.PTC_MAX_RESULT_BYTES", size):
            check_result_size(value)
        with patch("deep_agent.ptc.limits.PTC_MAX_RESULT_BYTES", size - 1):
            with self.assertRaises(ToolFailed):
                check_result_size(value)

    def test_real_32_mib_boundary_excludes_metadata_but_not_return_value(self):
        from deep_agent.ptc.limits import check_result_size, PTC_MAX_RESULT_BYTES
        from pydantic_ai import ToolFailed
        value = "x" * (PTC_MAX_RESULT_BYTES - 2)
        check_result_size(ToolReturn(value, metadata={"not_a_program_value": "y" * 100_000}))
        with self.assertRaises(ToolFailed):
            check_result_size(value + "x")

    def test_binary_and_non_json_leaves_keep_sdk_semantics(self):
        from datetime import datetime, timezone
        from decimal import Decimal
        from pydantic_ai import ToolFailed
        from deep_agent.ptc.limits import check_result_size
        check_result_size({"decimal": Decimal("1.25"), "at": datetime(2026, 1, 1, tzinfo=timezone.utc)})
        with patch("deep_agent.ptc.limits.PTC_MAX_RESULT_BYTES", 2):
            check_result_size(b"\xff\x00")
            with self.assertRaises(ToolFailed):
                check_result_size(b"\xff\x00\x01")

    def test_circular_value_fails_without_unbounded_copy(self):
        from pydantic_ai import ToolFailed
        from deep_agent.ptc.limits import check_result_size
        value = []
        value.append(value)
        with self.assertRaises(ToolFailed):
            check_result_size(value)


class PTCInternalCallsTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_native_call_cannot_inherit_program_bypass(self):
        async def query() -> dict:
            await asyncio.sleep(.01)
            return {"rows": [[i, "parallel-row"] for i in range(10_000)]}

        first = True
        def respond(messages, info):
            nonlocal first
            if first:
                first = False
                return ModelResponse(parts=[
                    ToolCallPart("run_code", {"code": "r = await demo_query()\nlen(r['rows'])"}, "ptc"),
                    ToolCallPart("demo_query", {}, "native"),
                ])
            return ModelResponse(parts=[TextPart("done")])

        limits, store = build_tool_output_limits(
            request_headers={}, context_window_tokens=40_000,
            max_output_tokens=8_000, estimator=TokenEstimator.conservative(),
        )
        deps = AgentDeps(session_id="parallel", run_id="parallel-run")
        store.bind(deps, [])
        store.write = AsyncMock(return_value="native-full-result")
        agent = Agent(FunctionModel(respond), deps_type=AgentDeps,
            toolsets=[FilePayloadResolvingToolset(FunctionToolset([query]).prefixed("demo"))],
            capabilities=[limits, HybridCodeMode(tools=[], dynamic_catalog=True)])
        result = await agent.run("parallel", deps=deps)
        parts = {p.tool_name: p for m in result.new_messages() for p in m.parts if isinstance(p, ToolReturnPart)}
        self.assertEqual(parts["run_code"].content, 10_000)
        self.assertEqual(parts["demo_query"].metadata["overflow_handle"], "native-full-result")
        store.write.assert_awaited_once()

    async def test_variables_cannot_cross_agent_runs(self):
        snippets = ["previous_run_only = 17\nprevious_run_only", "previous_run_only"]
        def respond(messages, info):
            if not any(isinstance(p, ToolReturnPart) for m in messages for p in m.parts):
                return ModelResponse(parts=[ToolCallPart("run_code", {"code": snippets.pop(0)}, "reused-id")])
            return ModelResponse(parts=[TextPart("done")])
        agent = Agent(FunctionModel(respond), deps_type=AgentDeps,
                      capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True)])
        first = await agent.run("first", deps=AgentDeps(session_id="separate"))
        second = await agent.run("second", deps=AgentDeps(session_id="separate"))
        p1 = next(p for m in first.new_messages() for p in m.parts if isinstance(p, ToolReturnPart))
        p2 = next(p for m in second.new_messages() for p in m.parts if isinstance(p, ToolReturnPart))
        self.assertEqual(p1.content, 17)
        self.assertEqual(p2.outcome, "failed")
        self.assertIn("previous_run_only", str(p2.content))

    async def run_case(self, code, *, native=False, tool=None, timeout=None):
        async def query() -> dict:
            return {"rows": [[i, "financial-row"] for i in range(10_000)]}

        requests = []
        def respond(messages, info):
            requests.append(messages)
            if len(requests) == 1:
                return ModelResponse(parts=[ToolCallPart(
                    "demo_query" if native else "run_code",
                    {} if native else {"code": code}, "outer",
                )])
            return ModelResponse(parts=[TextPart("done")])

        limits, store = build_tool_output_limits(
            request_headers={}, context_window_tokens=40_000,
            max_output_tokens=8_000, estimator=TokenEstimator.conservative(),
        )
        deps = AgentDeps(session_id="ptc-large-test", run_id="run-test")
        store.bind(deps, [])
        store.write = AsyncMock(return_value="synthetic-full-result")
        agent = Agent(
            FunctionModel(respond), deps_type=AgentDeps,
            toolsets=[build_code_mode_tool_package().toolset,
                      FilePayloadResolvingToolset(FunctionToolset([tool or query]).prefixed("demo"))],
            capabilities=[limits, HybridCodeMode(tools=[], dynamic_catalog=True)],
        )
        with patch("deep_agent.ptc.toolset.PTC_WALL_TIMEOUT_SECONDS", timeout or 120, create=True):
            result = await agent.run("process synthetic rows", deps=deps)
        parts = [p for m in result.new_messages() for p in m.parts if isinstance(p, ToolReturnPart)]
        return parts, store, deps, requests

    async def test_nested_large_result_reaches_program_without_spilling(self):
        parts, store, _, _ = await self.run_case("value = await demo_query()\nlen(value['rows'])")
        self.assertEqual(parts[0].content, 10_000)
        store.write.assert_not_awaited()
        self.assertLess(len(json.dumps(parts[0].metadata).encode()), 128 * 1024)

    async def test_native_large_result_still_spills(self):
        parts, store, _, _ = await self.run_case("", native=True)
        store.write.assert_awaited_once()
        self.assertEqual(parts[0].metadata["overflow_handle"], "synthetic-full-result")

    async def test_outer_large_return_still_spills_exactly_once(self):
        parts, store, _, _ = await self.run_case("await demo_query()")
        store.write.assert_awaited_once()
        self.assertEqual(parts[0].metadata["overflow_handle"], "synthetic-full-result")
        self.assertEqual(len(json.loads(store.write.await_args.args[1])["rows"]), 10_000)

    async def test_wait_timeout_is_failed_and_context_does_not_leak(self):
        closed = asyncio.Event()
        async def query() -> dict:
            try:
                await asyncio.sleep(10)
            finally:
                closed.set()
            return {}
        parts, store, _, _ = await self.run_case("await demo_query()", tool=query, timeout=.05)
        self.assertEqual(parts[0].outcome, "failed")
        self.assertTrue(closed.is_set())
        self.assertEqual(parts[0].metadata["ptc_diagnostics"]["summary"]["status"], "failed")
        from deep_agent.ptc.diagnostics import current_collector
        self.assertIsNone(current_collector.get())

    async def test_oversized_nested_return_is_not_a_truncated_success(self):
        async def query() -> dict:
            return {"text": "财务数据" * 100}
        with patch("deep_agent.ptc.limits.PTC_MAX_RESULT_BYTES", 100):
            parts, _, _, _ = await self.run_case("await demo_query()", tool=query)
        self.assertEqual(parts[0].outcome, "failed")
        self.assertIn("limit", str(parts[0].content).lower())


if __name__ == "__main__":
    unittest.main()
