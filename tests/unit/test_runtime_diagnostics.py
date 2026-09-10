from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent, DeferredToolRequests, FunctionToolset, ModelRetry
from pydantic_ai.exceptions import CallDeferred, ModelHTTPError, ToolFailed
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.diagnostics import RuntimeDiagnostics
from deep_agent.agent.runner import AgentRunner
from deep_agent.agent.event_persister import EventPersister
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class RuntimeDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def deps(self):
        return AgentDeps(session_id="diagnostic-session", agent_id="diagnostic-agent", run_id="diagnostic-run")

    async def test_actual_model_and_tool_results_identical_with_disabled_or_failed_sink(self):
        for sink in ("collect", "off", "broken"):
            with self.subTest(sink=sink):
                rows, requests, executions = [], [], []

                def model(messages, info):
                    requests.append([tool.name for tool in info.function_tools])
                    if len(messages) == 1:
                        return ModelResponse(parts=[ToolCallPart("sample", {"value": 4}, tool_call_id="c")])
                    return ModelResponse(parts=[TextPart("finished")])

                async def sample(value: int):
                    executions.append(value)
                    return {"value": value, "private": "result-not-logged"}

                def emit(event, **fields):
                    if sink == "broken":
                        raise RuntimeError("logging failure")
                    if sink == "collect":
                        rows.append((event, fields))

                with patch("deep_agent.agent.diagnostics.log_event", side_effect=emit):
                    agent = Agent(FunctionModel(model), deps_type=AgentDeps, tools=[sample],
                                  capabilities=[] if sink == "off" else [RuntimeDiagnostics()])
                    result = await agent.run("private prompt", deps=self.deps())
                self.assertEqual(result.output, "finished")
                self.assertEqual(executions, [4])
                self.assertEqual(requests, [["sample"], ["sample"]])
                if sink == "collect":
                    self.assertEqual([name for name, _ in rows], [
                        "model_request_started", "model_request_completed", "agent_tool_started",
                        "agent_tool_completed", "model_request_started", "model_request_completed",
                    ])
                    self.assertNotIn("private", repr(rows))
                    self.assertNotIn("result-not-logged", repr(rows))
                    self.assertTrue(all(item[1]["run_id"] == "diagnostic-run" for item in rows))
                    self.assertGreaterEqual(rows[3][1]["duration_ms"], 0)
        self.assertFalse([task for task in asyncio.all_tasks() if task.get_name() == "agent-diagnostic-wait"])

    async def test_retry_failure_and_deferred_preserve_framework_semantics(self):
        for mode in ("retry", "failed", "deferred", "schema"):
            with self.subTest(mode=mode):
                calls, rows, requests = [], [], []

                def model(messages, info):
                    requests.append(len(messages))
                    if len(requests) == 1:
                        return ModelResponse(parts=[ToolCallPart("sample", {"value": "wrong" if mode == "schema" else 1}, tool_call_id="c")])
                    if mode in {"retry", "schema"} and len(requests) == 2:
                        return ModelResponse(parts=[ToolCallPart("sample", {"value": 2}, tool_call_id="d")])
                    return ModelResponse(parts=[TextPart("done")])

                async def sample(value: int):
                    calls.append(value)
                    if mode == "retry" and value == 1:
                        raise ModelRetry("secret validation reason")
                    if mode == "failed":
                        raise ToolFailed("secret upstream reason")
                    if mode == "deferred":
                        raise CallDeferred()
                    return "private result"

                with patch("deep_agent.agent.diagnostics.log_event", side_effect=lambda name, **kw: rows.append((name, kw))):
                    agent = Agent(FunctionModel(model), deps_type=AgentDeps, tools=[sample],
                                  output_type=[str, DeferredToolRequests], capabilities=[RuntimeDiagnostics()])
                    result = await agent.run("secret prompt", deps=self.deps())
                names = [name for name, _ in rows]
                expected = {"retry": "agent_tool_retrying", "failed": "agent_tool_failed",
                            "deferred": "agent_tool_deferred", "schema": "tool_validation_retry"}[mode]
                self.assertIn(expected, names)
                self.assertEqual(calls, [1, 2] if mode == "retry" else [2] if mode == "schema" else [1])
                self.assertNotIn("secret", repr(rows))
                if mode == "deferred":
                    self.assertIsInstance(result.output, DeferredToolRequests)
                else:
                    self.assertEqual(result.output, "done")
                if mode == "failed":
                    self.assertTrue(any(isinstance(p, ToolReturnPart) and p.outcome == "failed"
                                        for m in result.new_messages() for p in m.parts))

    async def test_provider_error_identity_is_preserved(self):
        failure = ModelHTTPError(503, "fixture", "secret")

        def model(messages, info):
            raise failure

        rows = []
        with patch("deep_agent.agent.diagnostics.log_event", side_effect=lambda name, **kw: rows.append(name)):
            with self.assertRaises(ModelHTTPError) as raised:
                await Agent(FunctionModel(model), deps_type=AgentDeps, capabilities=[RuntimeDiagnostics()]).run("x", deps=self.deps())
        self.assertIs(raised.exception, failure)
        self.assertEqual(rows, ["model_request_started", "model_request_failed"])

    async def test_observation_metadata_and_watchdog_setup_errors_do_not_replace_result(self):
        class BrokenMetadata:
            @property
            def session_id(self):
                raise RuntimeError("diagnostic metadata failure")

            @property
            def usage(self):
                raise RuntimeError("diagnostic usage failure")

        capability = RuntimeDiagnostics()
        response = BrokenMetadata()
        context = SimpleNamespace(deps=BrokenMetadata(), model=SimpleNamespace(model_name="fixture"))
        requests = []

        async def handler(value):
            requests.append(value)
            return response

        with patch("deep_agent.agent.diagnostics.asyncio.create_task", side_effect=RuntimeError("task creation failed")), patch("deep_agent.agent.diagnostics.log_event", side_effect=RuntimeError("sink failed")):
            returned = await capability.wrap_model_request(context, request_context="same-request", handler=handler)
        self.assertIs(returned, response)
        self.assertEqual(requests, ["same-request"])

    async def test_parallel_tools_keep_independent_waiting_and_completion(self):
        rows, model_calls = [], []
        releases = [asyncio.Event(), asyncio.Event()]
        entered = [asyncio.Event(), asyncio.Event()]

        async def sample(value: int):
            entered[value].set()
            await releases[value].wait()
            return value

        def model(messages, info):
            model_calls.append(True)
            if len(model_calls) == 1:
                return ModelResponse(parts=[ToolCallPart("sample", {"value": value}, tool_call_id=f"c{value}") for value in (0, 1)])
            return ModelResponse(parts=[TextPart("done")])

        with patch("deep_agent.agent.diagnostics.log_event", side_effect=lambda name, **kw: rows.append((name, kw))):
            agent = Agent(FunctionModel(model), deps_type=AgentDeps, tools=[sample],
                          capabilities=[RuntimeDiagnostics(wait_after=0.01, wait_interval=0.01)])
            task = asyncio.create_task(agent.run("x", deps=self.deps()))
            try:
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), 1)
                releases[0].set()
                async with asyncio.timeout(1):
                    while not any(n == "agent_tool_still_waiting" and f["tool_call_id"] == "c1" for n, f in rows):
                        await asyncio.sleep(0.005)
                completed = [f["tool_call_id"] for n, f in rows if n == "agent_tool_completed"]
                self.assertEqual(completed, ["c0"])
                releases[1].set()
                self.assertEqual((await task).output, "done")
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_ptc_failure_is_observed_without_retry_or_result_changes(self):
        from deep_agent.ptc import HybridCodeMode
        from deep_agent.ptc.file_payload import build_code_mode_tool_package

        rows, calls, results = [], [], []

        async def stream(messages, info):
            calls.append(True)
            if len(calls) == 1:
                yield {0: DeltaToolCall(name="run_code", json_args=json.dumps({"code": "await search_tools(query='demo')"}), tool_call_id="ptc-call")}
            else:
                yield "native fallback remains available"

        with patch("deep_agent.agent.diagnostics.log_event", side_effect=lambda name, **kw: rows.append((name, kw))):
            agent = Agent(FunctionModel(stream_function=stream), deps_type=AgentDeps,
                          toolsets=[build_code_mode_tool_package().toolset],
                          capabilities=[RuntimeDiagnostics(), HybridCodeMode(tools=[], dynamic_catalog=True)])
            async with agent.run_stream_events("x", deps=self.deps()) as events:
                async for event in events:
                    if isinstance(getattr(event, "part", None), ToolReturnPart):
                        results.append(event.part)
        self.assertEqual(len(calls), 2)
        self.assertEqual(results[0].outcome, "failed")
        self.assertTrue(any(name == "agent_tool_failed" and fields["tool"] == "run_code" for name, fields in rows))
        self.assertNotIn("search_tools", repr(rows))

    async def test_external_ui_wait_returns_without_background_watchdog(self):
        from deep_agent.capabilities.ui.builtin import builtin_ui_records
        from deep_agent.capabilities.ui.registry import build_external_toolset

        rows = []

        def model(messages, info):
            return ModelResponse(parts=[ToolCallPart("builtin__confirm", {"message": "fixture"}, tool_call_id="ui-call")])

        with patch("deep_agent.agent.diagnostics.log_event", side_effect=lambda name, **kw: rows.append((name, kw))):
            agent = Agent(FunctionModel(model), deps_type=AgentDeps, output_type=[str, DeferredToolRequests],
                          toolsets=[build_external_toolset(builtin_ui_records())], capabilities=[RuntimeDiagnostics()])
            result = await agent.run("x", deps=self.deps())
        self.assertIsInstance(result.output, DeferredToolRequests)
        self.assertEqual(result.output.calls[0].tool_call_id, "ui-call")
        self.assertFalse([task for task in asyncio.all_tasks() if task.get_name() == "agent-diagnostic-wait"])
        self.assertNotIn("agent_tool_completed", [name for name, _ in rows])

    async def test_ptc_nested_success_and_outer_failure_preserve_audit_and_execution_count(self):
        from deep_agent.ptc import FilePayloadResolvingToolset, HybridCodeMode
        from deep_agent.ptc.file_payload import build_code_mode_tool_package

        rows, calls, steps = [], [], []

        async def lookup() -> dict[str, str]:
            calls.append(True)
            return {"value": "fixture-nested-body", "authorization": "Bearer fixture-secret"}

        def model(messages, info):
            steps.append(True)
            if len(steps) == 1:
                code = "1"
            elif len(steps) == 2:
                code = "value = await demo_lookup()\nmissing_after_side_effect"
            else:
                return ModelResponse(parts=[TextPart("done")])
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": code}, f"ptc-{len(steps)}")])

        with patch("deep_agent.agent.diagnostics.log_event", side_effect=lambda name, **kw: rows.append((name, kw))):
            agent = Agent(FunctionModel(model), deps_type=AgentDeps,
                          toolsets=[build_code_mode_tool_package().toolset,
                                    FilePayloadResolvingToolset(FunctionToolset(tools=[lookup]).prefixed("demo"))],
                          capabilities=[RuntimeDiagnostics(), HybridCodeMode(tools=[], dynamic_catalog=True)])
            result = await agent.run("x", deps=self.deps())
        self.assertEqual(result.output, "done")
        self.assertEqual(calls, [True])
        failed = next(p for m in result.new_messages() for p in m.parts
                      if isinstance(p, ToolReturnPart) and p.tool_call_id == "ptc-2")
        self.assertEqual(failed.outcome, "failed")
        self.assertTrue(failed.metadata["nested_calls"])
        self.assertTrue(any(n == "agent_tool_completed" and f["tool"] == "demo_lookup" for n, f in rows))
        self.assertTrue(any(n == "agent_tool_failed" and f["tool"] == "run_code" for n, f in rows))
        self.assertNotIn("fixture-nested-body", repr(rows))
        self.assertNotIn("fixture-secret", repr(rows))

    async def test_incremental_persistence_failure_logs_once_and_prevents_tool(self):
        stores, engine, _ = await make_sqlite_stores()
        agent_id = await seed_default_agent(stores)
        rows, calls = [], []

        async def sample():
            calls.append(True)
            return "must not execute"

        async def stream(messages, info):
            yield {0: DeltaToolCall(name="sample", json_args="{}", tool_call_id="c")}

        runner = AgentRunner(Agent(FunctionModel(stream_function=stream), deps_type=AgentDeps,
                                   tools=[sample], capabilities=[RuntimeDiagnostics()]),
                             stores.coordinator, UsageLimits(request_limit=5))
        persister = EventPersister(stores.coordinator)
        await persister.start()
        try:
            with patch("deep_agent.diagnostics.emit", side_effect=lambda name, **kw: rows.append((name, kw))), patch.object(stores.coordinator, "append_message_parts", new=AsyncMock(side_effect=OSError("fixture storage failure"))), patch.object(AgentRunner, "_generate_model_title", return_value=None):
                events = [event async for event in runner.run_stream("diag-write-failure", "x", AgentDeps(session_id="diag-write-failure", agent_id=agent_id, user="u"), persister=persister)]
            self.assertEqual(calls, [])
            self.assertEqual(events[-1]["type"], "error")
            failures = [fields for name, fields in rows if name == "message_persistence_failed" and fields.get("phase") == "incremental"]
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["error_type"], "OSError")
            self.assertNotIn("fixture storage failure", repr(rows))
        finally:
            await persister.aclose()
            await engine.dispose()

    async def test_streaming_watchdog_and_cancellation_clean_up(self):
        rows, entered = [], asyncio.Event()

        async def stream(messages, info):
            entered.set()
            yield "partial"
            await asyncio.Event().wait()

        async def run():
            agent = Agent(FunctionModel(stream_function=stream), deps_type=AgentDeps,
                          capabilities=[RuntimeDiagnostics(wait_after=0.01, wait_interval=0.01)])
            async with agent.run_stream_events("x", deps=self.deps()) as events:
                async for _ in events:
                    pass

        with patch("deep_agent.agent.diagnostics.log_event", side_effect=lambda name, **kw: rows.append(name)):
            task = asyncio.create_task(run())
            try:
                await asyncio.wait_for(entered.wait(), 1)
                async with asyncio.timeout(1):
                    while rows.count("model_request_still_waiting") < 2:
                        await asyncio.sleep(0.005)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertIn("model_request_cancelled", rows)
        self.assertNotIn("model_request_completed", rows)
        self.assertFalse([task for task in asyncio.all_tasks() if task.get_name() == "agent-diagnostic-wait"])

    async def test_real_runner_records_model_completion_before_next_tool_finishes(self):
        stores, engine, _ = await make_sqlite_stores()
        agent_id = await seed_default_agent(stores)
        entered, release, rows = asyncio.Event(), asyncio.Event(), []

        async def sample():
            entered.set()
            await release.wait()
            return "safe fixture"

        model_calls = []

        async def stream(messages, info):
            model_calls.append(True)
            if len(model_calls) == 1:
                yield {0: DeltaToolCall(name="sample", json_args="{}", tool_call_id="c")}
            else:
                yield "finished"

        runner = AgentRunner(Agent(FunctionModel(stream_function=stream), deps_type=AgentDeps,
                                   tools=[sample], capabilities=[RuntimeDiagnostics()]),
                             stores.coordinator, UsageLimits(request_limit=5))
        persister = EventPersister(stores.coordinator)
        await persister.start()

        async def run():
            return [event async for event in runner.run_stream("diag-live", "x", AgentDeps(session_id="diag-live", agent_id=agent_id, user="u"), persister=persister)]

        with patch("deep_agent.agent.diagnostics.log_event", side_effect=lambda name, **kw: rows.append(name)), patch.object(AgentRunner, "_generate_model_title", return_value=None):
            task = asyncio.create_task(run())
            try:
                await asyncio.wait_for(entered.wait(), 2)
                self.assertIn("model_request_completed", rows)
                self.assertIn("agent_tool_started", rows)
                self.assertNotIn("agent_tool_completed", rows)
                self.assertFalse(task.done())
                release.set()
                events = await asyncio.wait_for(task, 3)
                self.assertEqual(events[-1]["type"], "final")
                self.assertEqual(rows.count("model_request_completed"), 2)
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await persister.aclose()
                await engine.dispose()
