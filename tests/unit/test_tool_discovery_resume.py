from __future__ import annotations

import json
import unittest
from dataclasses import replace

from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, RunContext
from pydantic_ai.messages import (
    ModelResponse, TextPart, ToolCallPart,
    ToolSearchCallPart, ToolSearchReturnPart, UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import Tool, ToolDefinition
from pydantic_ai.toolsets import ExternalToolset, FunctionToolset
from pydantic_ai.usage import RunUsage

from deep_agent.agent.attachments import resolve_history_for_llm
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_discovery import (
    DeepAgentToolSearch, ToolDiscoveryTelemetry,
    build_mcp_discovery_toolset, search_deferred_tools,
)
from deep_agent.db.serialization import deserialize_message, serialize_message


SAVE = "deepfos_personal_report_save"


class ToolDiscoveryResumeTests(unittest.IsolatedAsyncioTestCase):
    def _ctx(self):
        return RunContext(
            deps=AgentDeps(session_id="discovery-test", agent_id="test"),
            model=TestModel(), usage=RunUsage(), prompt="继续",
        )

    def _definition(self, name=SAVE):
        return ToolDefinition(
            name=name, description="Save personal report",
            parameters_json_schema={"type": "object", "properties": {}},
            defer_loading=True,
        )

    def test_lost_evidence_can_repeat_same_query(self):
        ctx = self._ctx()
        tools = [self._definition()]
        self.assertEqual(search_deferred_tools(ctx, [SAVE], tools), [SAVE])
        ctx.discovered_tool_names.add(SAVE)
        self.assertEqual(search_deferred_tools(ctx, [SAVE], tools), [])
        ctx.discovered_tool_names.clear()
        self.assertFalse(ctx.is_tool_available(tools[0]))
        self.assertEqual(search_deferred_tools(ctx, [SAVE], tools), [SAVE])

    def test_stale_telemetry_cannot_hide_an_unavailable_tool(self):
        ctx = self._ctx()
        ctx.deps.tool_discovery = ToolDiscoveryTelemetry(
            discovered_tools=[SAVE], preselected_tools=[SAVE],
        )
        self.assertEqual(search_deferred_tools(ctx, [SAVE], [self._definition()]), [SAVE])

    def test_repeat_query_does_not_reveal_next_batch(self):
        ctx = self._ctx()
        tools = [self._definition(f"deepfos_report_save_{i}") for i in range(9)]
        first = search_deferred_tools(ctx, ["report save"], tools)
        self.assertEqual(len(first), 5)
        ctx.discovered_tool_names.update(first)
        self.assertEqual(search_deferred_tools(ctx, ["report save"], tools), [])
        ctx.discovered_tool_names.remove(first[0])
        self.assertEqual(search_deferred_tools(ctx, ["report save"], tools), [first[0]])

    def test_cached_match_cannot_expand_authorization(self):
        ctx = self._ctx()
        tools = [self._definition()]
        self.assertEqual(search_deferred_tools(ctx, [SAVE], tools), [SAVE])
        self.assertEqual(search_deferred_tools(ctx, [SAVE], []), [])

    def test_exact_available_tool_does_not_reveal_unrelated_tools(self):
        ctx = self._ctx()
        ctx.discovered_tool_names.add(SAVE)
        tools = [self._definition(), self._definition("deepfos_pipeline_save")]
        self.assertEqual(search_deferred_tools(ctx, [SAVE], tools), [])
        self.assertIn("已可用", next(iter(ctx.deps.tool_discovery.search_feedback.values())))

    def test_missing_identifier_does_not_fall_back_to_other_save_tools(self):
        ctx = self._ctx()
        tools = [self._definition("deepfos_pipeline_save"), self._definition("deepfos_script_save")]
        for queries in ([SAVE], ["保存个人报表", SAVE, "save personal report"]):
            self.assertEqual(search_deferred_tools(ctx, queries, tools), [])
        self.assertTrue(all("精确名称不在" in text for text in ctx.deps.tool_discovery.search_feedback.values()))

    def test_fuzzy_search_does_not_rank_only_remaining_tools(self):
        ctx = self._ctx()
        tools = [self._definition(f"deepfos_report_save_{i}") for i in range(9)]
        first = search_deferred_tools(ctx, ["report save"], tools)
        ctx.discovered_tool_names.update(first)
        self.assertEqual(search_deferred_tools(ctx, ["save report"], tools), [])

    def test_wrong_server_prefix_is_not_an_exact_match(self):
        ctx = self._ctx()
        ctx.deps.tool_discovery = ToolDiscoveryTelemetry(server_names=("deepfos",))
        self.assertEqual(search_deferred_tools(ctx, ["other_personal_report_save"], [self._definition()]), [])
        self.assertEqual(search_deferred_tools(ctx, ["personal_report_save"], [self._definition()]), [SAVE])

    def test_exact_match_and_missing_name_are_reported_together(self):
        ctx = self._ctx()
        self.assertEqual(search_deferred_tools(ctx, [SAVE, "deepfos_missing_get"], [self._definition()]), [SAVE])
        feedback = next(iter(ctx.deps.tool_discovery.search_feedback.values()))
        self.assertIn("已加载", feedback)
        self.assertIn("deepfos_missing_get", feedback)

    def test_distinct_protocol_actions_do_not_share_cached_matches(self):
        ctx = self._ctx()
        tools = [self._definition(SAVE), self._definition("deepfos_personal_report_create")]
        self.assertEqual(search_deferred_tools(ctx, [SAVE], tools), [SAVE])
        self.assertEqual(search_deferred_tools(ctx, ["deepfos_personal_report_create"], tools), ["deepfos_personal_report_create"])

    async def test_preselected_and_missing_search_feedback_survives_real_sdk(self):
        calls = 0
        executions = []

        def model(messages, info):
            nonlocal calls
            calls += 1
            self.assertIn(SAVE, {tool.name for tool in info.function_tools})
            if calls == 1:
                return ModelResponse(parts=[
                    ToolCallPart("search_tools", {"queries": [SAVE]}, "available"),
                    ToolCallPart("search_tools", {"queries": ["deepfos_missing_report_save"]}, "missing"),
                ])
            if calls == 2:
                results = {part.tool_call_id: part for message in messages for part in message.parts
                           if isinstance(part, ToolSearchReturnPart)}
                self.assertEqual(set(results), {"available", "missing"})
                self.assertIn("已可用", results["available"].content["message"])
                self.assertIn("精确名称不在", results["missing"].content["message"])
                for part in results.values():
                    self.assertEqual(part.discovered_tools, [])
                # Feedback and typed evidence survive the real event persistence codec.
                reloaded = [deserialize_message(serialize_message(message)) for message in messages]
                self.assertTrue(any("已可用" in str(part.content)
                                    for message in reloaded for part in message.parts
                                    if isinstance(part, ToolSearchReturnPart)))
                return ModelResponse(parts=[ToolCallPart(SAVE, {}, "save")])
            return ModelResponse(parts=[TextPart("complete")])

        result = await self._agent(model, executions).run(
            "保存个人报表 Save personal report", deps=AgentDeps(session_id="discovery-test", agent_id="test"),
        )
        self.assertEqual(result.output, "complete")
        self.assertEqual(executions, [SAVE])

    def _agent(self, model_fn, executions):
        def save() -> str:
            executions.append(SAVE)
            return "saved"

        def inspect() -> str:
            return "inspected"

        # More than ten MCP tools exercises real deferred/preselection assembly.
        tools = [Tool(save, name=SAVE, description="保存个人报表。Save personal report")] + [
            Tool(inspect, name=f"deepfos_inspect_{i}") for i in range(11)
        ]
        wrapper = build_mcp_discovery_toolset(
            [FunctionToolset(tools=tools)], server_names=["deepfos"],
            estimator=TokenEstimator.conservative(),
        )
        return Agent(
            FunctionModel(model_fn), deps_type=AgentDeps,
            output_type=[str, DeferredToolRequests],
            toolsets=[wrapper, ExternalToolset([ToolDefinition(
                name="ui_confirm", parameters_json_schema={"type": "object", "properties": {}},
            )])],
            capabilities=[DeepAgentToolSearch(strategy=search_deferred_tools)],
        )

    async def _pending_history(self):
        count = 0
        executions = []

        def model(_messages, info):
            nonlocal count
            count += 1
            if count == 1:
                self.assertNotIn(SAVE, {tool.name for tool in info.function_tools})
                return ModelResponse(parts=[ToolCallPart("search_tools", {"queries": [SAVE]}, "search-save")])
            self.assertIn(SAVE, {tool.name for tool in info.function_tools})
            return ModelResponse(parts=[ToolCallPart("ui_confirm", {}, "confirm-save")])

        deps = AgentDeps(session_id="discovery-test", agent_id="test")
        result = await self._agent(model, executions).run("继续", deps=deps)
        self.assertIsInstance(result.output, DeferredToolRequests)
        self.assertEqual(executions, [])
        # Actual persistence codec plus JSON, not direct in-memory reuse.
        history = [deserialize_message(json.loads(json.dumps(serialize_message(message))))
                   for message in result.all_messages()]
        self.assertTrue(any(isinstance(part, ToolSearchReturnPart)
                            for message in history for part in message.parts))
        return history, deps

    async def test_deferred_json_reload_preserves_disclosure_and_execution(self):
        history, _ = await self._pending_history()
        executions = []
        count = 0

        def model(_messages, info):
            nonlocal count
            count += 1
            self.assertIn(SAVE, {tool.name for tool in info.function_tools})
            if count == 1:
                return ModelResponse(parts=[ToolCallPart(SAVE, {}, "save-after-confirm")])
            return ModelResponse(parts=[TextPart("complete")])

        result = await self._agent(model, executions).run(
            message_history=history,
            deps=AgentDeps(session_id="discovery-test", agent_id="test"),
            deferred_tool_results=DeferredToolResults(calls={"confirm-save": {"confirmed": True}}),
        )
        self.assertEqual(result.output, "complete")
        self.assertEqual(executions, [SAVE])

    async def test_deferred_reload_can_recover_removed_evidence_with_old_telemetry(self):
        history, deps = await self._pending_history()
        # Explicitly simulate invalidated evidence; this is not a claim about the
        # observed online run, whose causal runtime trace is unavailable.
        history = [replace(message, parts=[part for part in message.parts
                    if not isinstance(part, (ToolSearchCallPart, ToolSearchReturnPart))])
                   for message in history]
        history = [message for message in history if message.parts]
        executions = []
        count = 0

        def model(_messages, info):
            nonlocal count
            count += 1
            if count == 1:
                self.assertNotIn(SAVE, {tool.name for tool in info.function_tools})
                return ModelResponse(parts=[ToolCallPart("search_tools", {"queries": [SAVE]}, "search-save-again")])
            self.assertIn(SAVE, {tool.name for tool in info.function_tools})
            if count == 2:
                return ModelResponse(parts=[ToolCallPart(SAVE, {}, "save-recovered")])
            return ModelResponse(parts=[TextPart("recovered")])

        result = await self._agent(model, executions).run(
            message_history=history, deps=deps,
            deferred_tool_results=DeferredToolResults(calls={"confirm-save": {"confirmed": True}}),
        )
        self.assertEqual(result.output, "recovered")
        self.assertEqual(executions, [SAVE])

    async def test_preselected_save_can_be_found_after_ui_resume_with_view_context(self):
        executions = []
        prompt = "照这个表单结构做一份个人报表"

        def first(_messages, info):
            self.assertIn(SAVE, {tool.name for tool in info.function_tools})
            return ModelResponse(parts=[ToolCallPart("ui_confirm", {}, "confirm-save")])

        result = await self._agent(first, executions).run(
            prompt, deps=AgentDeps(session_id="discovery-test", agent_id="test"),
        )
        self.assertIsInstance(result.output, DeferredToolRequests)
        history = [deserialize_message(json.loads(json.dumps(serialize_message(message))))
                   for message in result.all_messages()]
        # Use a synthetic structured reference through the real model projection.
        # The View dimension is data, not a request to restrict available tools.
        for message in history:
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    part.content = [{
                        "kind": "context-ref", "key": "sample", "type": "test.form",
                        "schema_version": "1", "label": "表单",
                        "payload": {"dimensionName": "View", "memberCode": "Periodic"},
                    }, prompt]
        history = await resolve_history_for_llm(
            history, current_user_index=None, request_headers={},
        )
        count = 0

        def resumed(_messages, info):
            nonlocal count
            count += 1
            if count == 1:
                return ModelResponse(parts=[ToolCallPart(
                    "search_tools", {"queries": [SAVE]}, "search-after-confirm",
                )])
            self.assertIn(SAVE, {tool.name for tool in info.function_tools})
            if count == 2:
                return ModelResponse(parts=[ToolCallPart(SAVE, {}, "save-after-confirm")])
            return ModelResponse(parts=[TextPart("saved")])

        result = await self._agent(resumed, executions).run(
            message_history=history,
            deps=AgentDeps(session_id="discovery-test", agent_id="test"),
            deferred_tool_results=DeferredToolResults(calls={"confirm-save": {"confirmed": True}}),
        )
        self.assertEqual(result.output, "saved")
        self.assertEqual(executions, [SAVE])

    async def test_dynamic_preselection_remains_directly_available(self):
        executions = []

        def model(_messages, info):
            self.assertIn(SAVE, {tool.name for tool in info.function_tools})
            return ModelResponse(parts=[TextPart("ready")])

        deps = AgentDeps(session_id="discovery-test", agent_id="test",
                         tool_discovery_inputs_explicit=True,
                         tool_discovery_primary_query=SAVE)
        await self._agent(model, executions).run(SAVE, deps=deps)
        self.assertIn(SAVE, deps.tool_discovery.preselected_tools)
        self.assertEqual(executions, [])


if __name__ == "__main__":
    unittest.main()
