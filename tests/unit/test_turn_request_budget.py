from __future__ import annotations

import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pydantic_ai import Agent, DeferredToolResults
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.native_tools import WebSearchTool
from pydantic_ai.tools import Tool, ToolDefinition
from pydantic_ai.usage import RunUsage, UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.request_budget import BUDGET_METADATA, TurnRequestBudget, restored_request_count
from deep_agent.options import Agent as AgentOptions
from deep_agent.agent.runner import AgentRunner
from deep_agent.agent.tool_discovery import DeepAgentToolSearch, search_deferred_tools
from deep_agent.capabilities.ui.builtin import build_ui_toolset
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class SpendSummaryRequest(AbstractCapability):
    """Represent a compaction request charged before the main request gate."""

    async def before_model_request(self, ctx, request_context):
        if ctx.usage.requests == 0:
            ctx.usage.requests += 1
        return request_context


class TurnRequestBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, _ = await make_sqlite_stores()
        self.agent_id = await seed_default_agent(self.stores)
        self.persister = EventPersister(self.stores.coordinator)
        await self.persister.start()
        self.title_patch = patch.object(AgentRunner, "_generate_model_title", return_value=None)
        self.title_patch.start()

    async def asyncTearDown(self):
        self.title_patch.stop()
        await self.persister.aclose()
        await self.engine.dispose()

    def deps(self, session):
        return AgentDeps(session_id=session, agent_id=self.agent_id, user="budget-user")

    def runner(self, respond, *, limit, tools=(), ui=False, capabilities=()):
        async def stream(messages, info):
            response = respond(messages, info)
            for index, part in enumerate(response.parts):
                if isinstance(part, ToolCallPart):
                    yield {index: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args),
                                               tool_call_id=part.tool_call_id)}
                elif isinstance(part, TextPart):
                    yield part.content

        return AgentRunner(
            Agent(FunctionModel(respond, stream_function=stream), deps_type=AgentDeps,
                  tools=list(tools), toolsets=[build_ui_toolset()] if ui else [],
                  capabilities=list(capabilities)),
            self.stores.coordinator, UsageLimits(request_limit=limit),
            ui_tool_hints={"builtin__confirm": {"widget": "confirm"}} if ui else {},
        )

    async def execute(self, runner, session, *, streaming, message="分析", deferred=None):
        deps = self.deps(session)
        if streaming:
            events = [event async for event in runner.run_stream(
                session, message, deps, deferred_tool_results=deferred, persister=self.persister,
            )]
            await self.persister.flush(session, agent_id=self.agent_id)
            return events[-1], events, deps
        result = await runner.run(session, message, deps, deferred_tool_results=deferred)
        return result, [], deps

    async def facts(self, session, turn_id=None):
        return await self.stores.coordinator.list_events(session, turn_id=turn_id, limit=500)

    def assert_final(self, result, events):
        self.assertEqual(result.get("type", result.get("status")), "final" if events else "completed")
        self.assertIn("继续", result["output"])
        self.assertFalse([event for event in events if event["type"] == "error"])

    async def test_limits_one_two_three_reserve_last_request_in_both_runner_paths(self):
        for streaming in (False, True):
            for limit in (1, 2, 3):
                with self.subTest(streaming=streaming, limit=limit):
                    requests, calls = [], []

                    async def inspect_item(value: int):
                        calls.append(value)
                        return {"value": value}

                    def respond(messages, info):
                        requests.append(info)
                        if info.function_tools:
                            return ModelResponse(parts=[ToolCallPart("inspect_item", {"value": len(requests)},
                                                                     f"item-{len(requests)}")])
                        self.assertFalse(info.output_tools)
                        self.assertTrue(info.allow_text_output)
                        self.assertIn("单次回复", info.instructions)
                        self.assertIn("继续", info.instructions)
                        return ModelResponse(parts=[TextPart("已有结论；本轮到限，可回复继续。")])

                    session = f"limit-{streaming}-{limit}"
                    runner = self.runner(respond, limit=limit, tools=[inspect_item])
                    result, events, deps = await self.execute(runner, session, streaming=streaming)
                    self.assert_final(result, events)
                    self.assertEqual(len(requests), limit)
                    self.assertEqual(calls, list(range(1, limit)))
                    rows = await self.facts(session, deps.turn_id)
                    self.assertEqual(restored_request_count(rows), limit)
                    self.assertFalse([row for row in rows if row.event_type == "execution/error"])

    async def test_parallel_tools_cost_one_request_not_one_each(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                calls, requests = [], []

                async def inspect_item(value: int):
                    calls.append(value)
                    return value

                def respond(messages, info):
                    requests.append(info)
                    if info.function_tools:
                        return ModelResponse(parts=[TextPart("并行检查"), *[
                            ToolCallPart("inspect_item", {"value": index}, f"parallel-{index}")
                            for index in range(3)
                        ]])
                    return ModelResponse(parts=[TextPart("已完成检查，回复继续。")])

                session = f"parallel-{streaming}"
                result, events, deps = await self.execute(
                    self.runner(respond, limit=2, tools=[inspect_item]), session, streaming=streaming,
                )
                self.assert_final(result, events)
                self.assertEqual(sorted(calls), [0, 1, 2])
                self.assertEqual(len(requests), 2)
                rows = await self.facts(session, deps.turn_id)
                # One response has text and three tool parts: raw row count is not request count.
                self.assertGreater(len([r for r in rows if r.event_type == "assistant/message"]), 2)
                self.assertEqual(restored_request_count(rows), 2)
                checkpoints = [r.payload["message"]["metadata"][BUDGET_METADATA]["run_requests"]
                               for r in rows if r.event_type == "assistant/message"]
                self.assertEqual(max(checkpoints), 2)

    async def test_default_eighty_warns_after_sixty_four_and_finishes_on_eighty(self):
        requests, calls = [], []

        async def inspect_item():
            calls.append(True)
            return "still more items"

        def respond(messages, info):
            requests.append(info)
            if info.function_tools:
                return ModelResponse(parts=[ToolCallPart("inspect_item", {}, f"item-{len(requests)}")])
            return ModelResponse(parts=[TextPart("已完成部分检查，本轮到限，可回复继续。")])

        result, events, deps = await self.execute(
            self.runner(respond, limit=AgentOptions.max_requests.default, tools=[inspect_item]), "eighty", streaming=True,
        )
        self.assert_final(result, events)
        self.assertEqual(len(requests), 80)
        self.assertEqual(len(calls), 79)
        self.assertNotIn("剩余调用额度较少", requests[63].instructions or "")
        self.assertIn("剩余调用额度较少", requests[64].instructions)
        self.assertTrue(requests[78].function_tools)
        self.assertFalse(requests[79].function_tools)
        self.assertEqual(restored_request_count(await self.facts("eighty", deps.turn_id)), 80)

    async def test_ui_resume_after_errors_preserves_budget_and_next_user_continue_resets_it(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                requests = []

                def respond(messages, info):
                    requests.append(info)
                    if info.function_tools:
                        return ModelResponse(parts=[TextPart("请确认"), ToolCallPart(
                            "builtin__confirm", {"message": "确认本次操作"}, f"confirm-{len(requests)}",
                        )])
                    return ModelResponse(parts=[TextPart("已确认，回复继续可以接着处理。")])

                runner = self.runner(respond, limit=3, ui=True)
                session = f"resume-{streaming}"
                first, _, first_deps = await self.execute(runner, session, streaming=streaming)
                self.assertEqual(first["status"], "pending")
                for index, error_type in enumerate(("UserError", "AttributeError")):
                    await self.stores.coordinator.append_execution_error(
                        session, status="failed", agent_id=self.agent_id, user_id=first_deps.user,
                        turn_id=first_deps.turn_id, run_id=f"failed-resume-{index}",
                        payload={"code": "agent_run_failed", "phase": "history",
                                 "message": f"执行发生 {error_type}，本轮已停止。"},
                    )
                second, _, second_deps = await self.execute(
                    runner, session, streaming=streaming, message="",
                    deferred=DeferredToolResults(calls={"confirm-1": {"confirmed": True}}),
                )
                self.assertEqual(second["status"], "pending")
                final, events, final_deps = await self.execute(
                    runner, session, streaming=streaming, message="",
                    deferred=DeferredToolResults(calls={"confirm-2": {"confirmed": True}}),
                )
                self.assert_final(final, events)
                self.assertEqual(first_deps.turn_id, second_deps.turn_id)
                self.assertEqual(first_deps.turn_id, final_deps.turn_id)
                self.assertEqual(final_deps.request_budget.previous_requests, 2)
                self.assertEqual(len(requests), 3)
                self.assertEqual(restored_request_count(await self.facts(session, first_deps.turn_id)), 3)
                again, _, again_deps = await self.execute(runner, session, streaming=streaming, message="继续")
                self.assertEqual(again["status"], "pending")
                self.assertNotEqual(again_deps.turn_id, first_deps.turn_id)
                self.assertEqual(again_deps.request_budget.previous_requests, 0)
                self.assertTrue(requests[-1].function_tools)

    async def test_ui_on_request_79_resumes_as_text_only_request_80(self):
        requests = []

        async def inspect_item():
            return "evidence"

        def respond(messages, info):
            requests.append(info)
            if len(requests) == 79:
                return ModelResponse(parts=[ToolCallPart(
                    "builtin__confirm", {"message": "确认已有结果"}, "boundary-confirm",
                )])
            if info.function_tools:
                return ModelResponse(parts=[ToolCallPart("inspect_item", {}, f"boundary-{len(requests)}")])
            return ModelResponse(parts=[TextPart("已确认并给出阶段性结果，可以回复继续。")])

        runner = self.runner(respond, limit=AgentOptions.max_requests.default, tools=[inspect_item], ui=True)
        first, _, deps = await self.execute(runner, "ui-boundary-80", streaming=True)
        self.assertEqual(first["status"], "pending")
        self.assertEqual(len(requests), 79)
        final, events, resumed = await self.execute(
            runner, "ui-boundary-80", streaming=True, message="",
            deferred=DeferredToolResults(calls={"boundary-confirm": {"confirmed": True}}),
        )
        self.assert_final(final, events)
        self.assertEqual(resumed.request_budget.previous_requests, 79)
        self.assertEqual(len(requests), 80)
        self.assertFalse(requests[-1].function_tools)
        self.assertEqual(restored_request_count(await self.facts("ui-boundary-80", deps.turn_id)), 80)

    async def test_compaction_spend_is_seen_by_final_gate_and_persisted_checkpoint(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                requests, calls = [], []

                async def inspect_item():
                    calls.append(True)
                    return "should not execute"

                def respond(messages, info):
                    requests.append(info)
                    self.assertFalse(info.function_tools)
                    return ModelResponse(parts=[TextPart("根据已有信息总结，可回复继续。")])

                session = f"compaction-{streaming}"
                result, events, deps = await self.execute(self.runner(
                    respond, limit=2, tools=[inspect_item], capabilities=[SpendSummaryRequest()],
                ), session, streaming=streaming)
                self.assert_final(result, events)
                self.assertEqual(len(requests), 1)
                self.assertFalse(calls)
                self.assertEqual(restored_request_count(await self.facts(session, deps.turn_id)), 2)

    async def test_provider_failure_on_final_request_is_not_reported_as_completed(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                requests = []

                async def inspect_item():
                    return "evidence"

                def respond(messages, info):
                    requests.append(info)
                    if info.function_tools:
                        return ModelResponse(parts=[ToolCallPart("inspect_item", {}, "failure-evidence")])
                    raise ModelHTTPError(503, "fixture-model", "fixture upstream unavailable")

                runner = self.runner(respond, limit=2, tools=[inspect_item])
                session = f"provider-error-{streaming}"
                with patch("deep_agent.agent.runner.logger.exception"):
                    if streaming:
                        result, events, _ = await self.execute(runner, session, streaming=True)
                        self.assertEqual(result["type"], "error")
                        self.assertNotIn("final", [e["type"] for e in events])
                    else:
                        with self.assertRaises(ModelHTTPError):
                            await self.execute(runner, session, streaming=False)
                self.assertEqual(len(requests), 2)
                self.assertTrue([row for row in await self.facts(session) if row.event_type == "execution/error"])

    async def test_failed_resume_diagnostic_message_does_not_break_budget_restore(self):
        rows = [
            SimpleNamespace(sequence=1, event_type="assistant/message", run_id="original",
                            payload={"message": {"kind": "response", "parts": [],
                                     "metadata": {BUDGET_METADATA: {"run_requests": 10}}}}),
            SimpleNamespace(sequence=2, event_type="execution/error", run_id="resume-1",
                            payload={"message": "执行发生 UserError，本轮已停止。", "phase": "agent"}),
            SimpleNamespace(sequence=3, event_type="execution/error", run_id="resume-2",
                            payload={"message": "执行发生 AttributeError，本轮已停止。", "phase": "history"}),
        ]
        self.assertEqual(restored_request_count(rows), 10)
        self.assertEqual(restored_request_count([vars(row) for row in rows]), 10)
        # An execution failure alone did not send any model request.
        self.assertEqual(restored_request_count(rows[1:]), 0)

    async def test_legacy_compaction_telemetry_is_not_counted_per_event(self):
        rows = [SimpleNamespace(run_id="legacy-run", event_type="compaction", sequence=index,
                                payload={"budget": {"summary_calls": count}})
                for index, count in enumerate((1, 2, 2), start=1)]
        self.assertEqual(restored_request_count(rows), 2)

    async def test_provider_ignoring_no_tools_cannot_execute_a_known_tool(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                calls, requests = [], []

                async def write_record():
                    calls.append("unexpected side effect")
                    return "written"

                def respond(messages, info):
                    requests.append(info)
                    self.assertFalse(info.function_tools)
                    return ModelResponse(parts=[ToolCallPart("write_record", {}, "forbidden-final-call")])

                runner = self.runner(respond, limit=1, tools=[write_record])
                session = f"unexpected-tool-{streaming}"
                with patch("deep_agent.agent.runner.logger.exception"):
                    if streaming:
                        result, events, _ = await self.execute(runner, session, streaming=True)
                        self.assertEqual(result["type"], "error")
                        self.assertFalse([e for e in events if e["type"] == "tool_call"])
                    else:
                        with self.assertRaises(UnexpectedModelBehavior):
                            await self.execute(runner, session, streaming=False)
                self.assertFalse(calls)
                self.assertEqual(len(requests), 1)

    async def test_final_gate_clears_native_output_and_discovery_state(self):
        deferred = ToolDefinition(name="deferred_query", defer_loading=True)
        parameters = ModelRequestParameters(
            function_tools=[deferred], native_tools=[WebSearchTool()],
            output_tools=[ToolDefinition(name="structured_answer", kind="output")],
            output_mode="tool", allow_text_output=False,
            tool_visibility={"deferred_query": "visible"},
            revealed_tool_names={"deferred_query"}, deferred_capability_ids={"deferred-package"},
        )
        request = ModelRequestContext(
            model=FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("done")])),
            messages=[ModelRequest(parts=[UserPromptPart("analyse")])],
            model_settings={"tool_choice": "required"}, model_request_parameters=parameters,
        )
        budget = TurnRequestBudget(UsageLimits(request_limit=40), previous_requests=38)
        await budget.before_model_request(SimpleNamespace(usage=RunUsage(requests=1)), request)
        final = request.model_request_parameters
        self.assertEqual(final.function_tools, [])
        self.assertEqual(final.native_tools, [])
        self.assertEqual(final.output_tools, [])
        self.assertFalse(final.revealed_tool_names)
        self.assertFalse(final.deferred_capability_ids)
        self.assertFalse(final.tool_visibility)
        self.assertEqual(final.output_mode, "text")
        self.assertTrue(final.allow_text_output)
        self.assertEqual(request.model_settings["tool_choice"], "none")

    async def test_tools_discovered_in_previous_response_cannot_return_on_final_request(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                requests, executions = [], []

                def deferred_query():
                    executions.append(True)
                    return "data"

                def respond(messages, info):
                    requests.append(info)
                    if len(requests) == 1:
                        self.assertIn("search_tools", {tool.name for tool in info.function_tools})
                        return ModelResponse(parts=[ToolCallPart("search_tools", {"queries": ["deferred_query"]}, "discover")])
                    self.assertFalse(info.function_tools)
                    return ModelResponse(parts=[TextPart("已定位工具，回复继续可以查询。")])

                runner = self.runner(respond, limit=2,
                                     tools=[Tool(deferred_query, defer_loading=True)],
                                     capabilities=[DeepAgentToolSearch(strategy=search_deferred_tools)])
                result, events, _ = await self.execute(runner, f"dynamic-final-{streaming}", streaming=streaming)
                self.assert_final(result, events)
                self.assertEqual(len(requests), 2)
                self.assertFalse(executions)

    async def test_provider_overflow_retry_keeps_consumed_request(self):
        requests, executions, recovered_usage = [], [], []

        async def inspect_item():
            executions.append(True)
            return "data"

        def respond(messages, info):
            requests.append(info)
            if len(requests) == 1:
                self.assertTrue(info.function_tools)
                raise ModelHTTPError(400, "fixture-model", "maximum context length exceeded")
            self.assertFalse(info.function_tools)
            return ModelResponse(parts=[TextPart("基于已有材料总结，可回复继续。")])

        async def recover(session, deps, *, message_history, raw_history, usage):
            recovered_usage.append(usage.requests)
            return message_history, SimpleNamespace(public_dict=lambda: {"event_type": "compaction", "status": "completed"})

        runner = self.runner(respond, limit=2, tools=[inspect_item])
        with patch.object(runner, "_recover_provider_overflow", side_effect=recover):
            result, events, deps = await self.execute(runner, "overflow-retry", streaming=True)
        self.assert_final(result, events)
        self.assertEqual(recovered_usage, [1])
        self.assertEqual(len(requests), 2)
        self.assertFalse(executions)
        self.assertEqual(restored_request_count(await self.facts("overflow-retry", deps.turn_id)), 2)


if __name__ == "__main__":
    unittest.main()
