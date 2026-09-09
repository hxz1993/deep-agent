from __future__ import annotations

import asyncio
import json
from contextlib import ExitStack
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent, ModelRetry
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.active_runs import ActiveRunRegistry
from deep_agent.agent.diagnostics import RuntimeDiagnostics
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.runner import AgentRunner
from deep_agent.diagnostics import runtime
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


_VOLATILE = frozenset({
    "timestamp", "created_at", "updated_at", "duration_ms", "run_id", "conversation_id",
    "agent_id", "message_id", "event_id", "execution_id", "provider_response_id",
})


def stable(value):
    if isinstance(value, dict):
        return {key: stable(item) for key, item in value.items() if key not in _VOLATILE}
    if isinstance(value, list):
        return [stable(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"(?<![a-f0-9])(?:[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})(?![a-f0-9])", "<uuid>", value)
    return value


class DiagnosticRunnerEquivalenceTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, mode: str, scenario: str):
        stores, engine, _ = await make_sqlite_stores()
        agent_id = await seed_default_agent(stores)
        persister = EventPersister(stores.coordinator)
        await persister.start()
        requests, calls, operational = [], [], []

        async def sample(value: int):
            calls.append(value)
            if value == 1:
                raise ModelRetry("fixture retry reason")
            return {"value": value * 2}

        async def stream(messages, info):
            requests.append({
                "messages": stable(ModelMessagesTypeAdapter.dump_python(messages, mode="json")),
                "tools": [{"name": tool.name, "schema": tool.parameters_json_schema}
                          for tool in info.function_tools],
            })
            step = len(requests)
            if step <= 2:
                yield f"阶段 {step}。"
                yield {0: DeltaToolCall(name="sample", json_args=json.dumps({"value": step}),
                                       tool_call_id=f"fixture-call-{step}")}
            elif scenario == "provider_error":
                yield "已完成工具，正在生成"
                raise ModelHTTPError(503, "fixture-model", "fixture provider failure")
            else:
                yield "最终"
                await asyncio.sleep(0)
                yield "答案"

        runner = AgentRunner(
            Agent(FunctionModel(stream_function=stream), deps_type=AgentDeps, tools=[sample],
                  capabilities=[RuntimeDiagnostics()]),
            stores.coordinator, UsageLimits(request_limit=6),
        )
        try:
            with TemporaryDirectory(prefix="TMP_to_delete_diagnostic_equivalence_") as tmp:
                writer = runtime.DiagnosticRuntime(
                    runtime.DiagnosticConfig(Path(tmp) / "diagnostics", enabled=mode != "disabled"), "api",
                )
                with ExitStack() as stack:
                    stack.enter_context(patch.object(runtime, "_runtime", writer))
                    stack.enter_context(patch.object(runtime, "mirror_operational",
                        side_effect=lambda data, level: operational.append(json.loads(data))))
                    stack.enter_context(patch.object(AgentRunner, "_generate_model_title", return_value=None))
                    stack.enter_context(patch("deep_agent.agent.runner.logger.exception"))
                    if mode == "write_error":
                        stack.enter_context(patch.object(writer, "_write", side_effect=OSError("fixture disk failure")))
                    writer.start()
                    try:
                        events = [event async for event in runner.run_stream(
                            "diagnostic-equivalence", "fixture question",
                            AgentDeps(session_id="diagnostic-equivalence", agent_id=agent_id, user="fixture-user"),
                            persister=persister,
                        )]
                        await persister.flush("diagnostic-equivalence", agent_id=agent_id)
                        history = await stores.coordinator.get_history("diagnostic-equivalence", agent_id=agent_id)
                        raw_history = await stores.coordinator.get_raw_history("diagnostic-equivalence", agent_id=agent_id)
                    finally:
                        # The real queue is drained off the asyncio thread, including
                        # failure/disabled modes; test teardown must not freeze SSE.
                        await asyncio.to_thread(writer.close)
                    self.assertFalse(writer.thread.is_alive())
                    self.assertEqual(writer.queue.unfinished_tasks, 0)
                    if mode == "enabled":
                        persisted = [json.loads(line) for path in writer.config.directory.glob("*.jsonl")
                                     for line in path.read_text().splitlines()]
                        self.assertEqual([row["event"] for row in persisted], [row["event"] for row in operational])
                    elif mode == "disabled":
                        self.assertFalse(writer.config.directory.exists())
                    else:
                        self.assertGreater(writer.status()["dropped_records"], 0)

                names = [row["event"] for row in operational]
                self.assertEqual(names.count("model_request_started"), 3)
                self.assertEqual(names.count("agent_tool_started"), 2)
                self.assertEqual(names.count("agent_tool_retrying"), 1)
                self.assertEqual(names.count("agent_tool_completed"), 1)
                self.assertEqual(calls, [1, 2])
                self.assertEqual(events[-1]["type"], "error" if scenario == "provider_error" else "final")
                if scenario == "provider_error":
                    self.assertEqual(names.count("agent_run_failed"), 1)
                else:
                    self.assertEqual(events[-1]["output"], "最终答案")
                self.assertFalse([task for task in asyncio.all_tasks() if task.get_name() == "agent-diagnostic-wait"])
                return {
                    "requests": requests, "tool_arguments": calls, "sse": stable(events),
                    "history": stable(ModelMessagesTypeAdapter.dump_python(history, mode="json")),
                    "raw_history": stable(ModelMessagesTypeAdapter.dump_python(raw_history, mode="json")),
                }
        finally:
            await persister.aclose()
            await engine.dispose()

    async def test_multiround_retry_has_identical_sse_history_and_calls_in_all_file_modes(self):
        expected = await self.exercise("enabled", "success")
        for mode in ("disabled", "write_error"):
            with self.subTest(mode=mode):
                self.assertEqual(await self.exercise(mode, "success"), expected)

    async def test_provider_failure_after_tools_has_identical_partial_history_in_all_file_modes(self):
        expected = await self.exercise("enabled", "provider_error")
        for mode in ("disabled", "write_error"):
            with self.subTest(mode=mode):
                self.assertEqual(await self.exercise(mode, "provider_error"), expected)

    async def terminal_case(self, streaming, case, broken_sink=False):
        from deep_agent.capabilities.ui.builtin import build_ui_toolset

        stores, engine, _ = await make_sqlite_stores()
        agent_id = await seed_default_agent(stores)
        persister = EventPersister(stores.coordinator)
        await persister.start()
        rows, calls, steps, out = [], [], [], []
        entered = asyncio.Event()
        registry = ActiveRunRegistry()
        failure = ModelHTTPError(503, "fixture", "do-not-log-this-body")

        async def slow():
            calls.append("slow")
            entered.set()
            await asyncio.Event().wait()

        def respond(messages, info):
            steps.append("model")
            if case == "provider":
                raise failure
            if case == "cancel":
                return ModelResponse(parts=[ToolCallPart("slow", {}, "fixture-slow")])
            if case == "pending":
                return ModelResponse(parts=[ToolCallPart("builtin__confirm", {"message": "fixture"}, "fixture-ui")])
            return ModelResponse(parts=[TextPart("done")])

        async def stream(messages, info):
            response = respond(messages, info)
            for part in response.parts:
                if isinstance(part, ToolCallPart):
                    yield {0: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args), tool_call_id=part.tool_call_id)}
                else:
                    yield part.content

        agent = Agent(FunctionModel(respond, stream_function=stream), deps_type=AgentDeps,
                      tools=[slow], toolsets=[build_ui_toolset()] if case == "pending" else [],
                      capabilities=[RuntimeDiagnostics()])
        runner = AgentRunner(agent, stores.coordinator, UsageLimits(request_limit=5), active_runs=registry,
                             ui_tool_hints={"builtin__confirm": {"widget": "confirm"}})
        deps = AgentDeps(session_id="terminal-fixture", agent_id=agent_id, user="fixture-user")

        def capture(event, **fields):
            if broken_sink:
                raise RuntimeError("diagnostic failure")
            rows.append((event, fields))

        async def execute():
            try:
                if streaming:
                    async for event in runner.run_stream(deps.session_id, "fixture", deps, persister=persister):
                        out.append(event)
                else:
                    out.append(await runner.run(deps.session_id, "fixture", deps))
            except (Exception, asyncio.CancelledError) as exc:
                out.append({"raised": type(exc).__name__})
                if case == "provider":
                    self.assertIs(exc, failure)

        try:
            with ExitStack() as stack:
                stack.enter_context(patch("deep_agent.diagnostics.emit", side_effect=capture))
                stack.enter_context(patch.object(AgentRunner, "_generate_model_title", return_value=None))
                stack.enter_context(patch("deep_agent.agent.runner.logger.exception"))
                stack.enter_context(patch("deep_agent.agent.runner.logger.warning"))
                if case in ("history", "input"):
                    stack.enter_context(patch.object(runner, "_prepare_history" if case == "history" else "_llm_inputs",
                                                    new=AsyncMock(side_effect=ValueError("fixture preparation failure"))))
                if case == "persistence":
                    stack.enter_context(patch.object(persister if streaming else runner,
                                                    "flush" if streaming else "_persist_run_messages",
                                                    new=AsyncMock(side_effect=OSError("fixture persistence failure"))))
                task = asyncio.create_task(execute())
                try:
                    if case == "cancel":
                        await asyncio.wait_for(entered.wait(), 3)
                        if streaming:
                            self.assertTrue(registry.request_cancel(agent_id=agent_id, session_id=deps.session_id, user_id=deps.user))
                        else:
                            task.cancel()
                    await asyncio.wait_for(task, 3)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                history = await stores.coordinator.get_raw_history(deps.session_id, agent_id=agent_id)
            expected = "agent_run_pending" if case == "pending" else "agent_run_cancelled" if case == "cancel" else "agent_run_failed"
            if not broken_sink:
                terminals = [(name, fields) for name, fields in rows
                             if name in {"agent_run_completed", "agent_run_pending", "agent_run_cancelled", "agent_run_failed"}]
                self.assertEqual([name for name, _ in terminals], [expected])
                self.assertNotIn("agent_tool_event", [name for name, _ in rows])
                self.assertNotIn("do-not-log-this-body", repr(rows))
                self.assertEqual(terminals[0][1]["run_id"], deps.run_id)
            if case == "cancel":
                self.assertEqual(calls, ["slow"])
                if streaming:
                    self.assertEqual(out[-1]["type"], "cancelled")
                else:
                    self.assertEqual(out[-1], {"raised": "CancelledError"})
            if case == "pending":
                self.assertEqual(out[-1]["status"], "pending")
                if streaming:
                    self.assertEqual(sum(event.get("type") == "tool_call" for event in out), 1)
            return stable({"output": out, "calls": calls, "steps": steps,
                           "history": ModelMessagesTypeAdapter.dump_python(history, mode="json")})
        finally:
            await persister.aclose()
            await engine.dispose()

    async def test_terminal_diagnostics_are_exactly_once_and_fail_open_for_both_runners(self):
        for streaming in (False, True):
            for case in ("provider", "cancel", "pending", "history", "input", "persistence"):
                with self.subTest(streaming=streaming, case=case):
                    expected = await self.terminal_case(streaming, case)
                    self.assertEqual(await self.terminal_case(streaming, case, broken_sink=True), expected)
