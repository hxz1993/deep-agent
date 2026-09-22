from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest

from pydantic_ai import Agent
from pydantic_ai.messages import (
    BinaryContent, InstructionPart, ModelRequest, ModelResponse,
    TextPart, ToolCallPart, ToolReturnPart, UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RequestUsage

from deep_agent.agent.context.estimator import TokenEstimator, USAGE_ANCHOR_METADATA
from deep_agent.agent.context.compaction import DeepAgentContextCompaction
from deep_agent.agent.context.runtime import ContextRunState
from deep_agent.db.serialization import serialize_message, deserialize_message


class UsageAnchorTests(unittest.TestCase):
    def setUp(self):
        self.estimator = TokenEstimator.for_model("test-model", provider_type="private")
        self.params = ModelRequestParameters(instruction_parts=[InstructionPart("保持准确")])
        self.request = ModelRequest(parts=[UserPromptPart("问题")], instructions="保持准确")
        self.response = ModelResponse(parts=[TextPart("答案")], usage=RequestUsage(input_tokens=10000, output_tokens=100))
        self.estimator.record_usage_anchor([self.request], self.params, self.response)
        self.history = [self.request, self.response]

    def test_provider_anchor_and_only_new_content_are_counted(self):
        baseline = self.estimator.estimate_request(self.history, self.params)
        self.assertEqual(baseline.tokens, 10100)
        suffix = ModelRequest(parts=[ToolReturnPart("search", "结果 JSON {\"a\":123}", "c")])
        result = self.estimator.estimate_request([*self.history, suffix], self.params)
        self.assertEqual(result.tokens, baseline.tokens + self.estimator.message(suffix))
        self.assertEqual(result.source, "provider_usage_plus_estimate")

    def test_repeated_instructions_and_parameters_count_once(self):
        repeated = [ModelRequest(parts=[UserPromptPart("中文")], instructions="指令" * 100) for _ in range(3)]
        single = [replace(m, instructions=None) for m in repeated[:-1]] + repeated[-1:]
        self.assertEqual(self.estimator.request(repeated), self.estimator.request(single))
        params = ModelRequestParameters(instruction_parts=[InstructionPart("指令" * 100)])
        self.assertEqual(self.estimator.request(repeated, params)[0], self.estimator.request(single)[0])
        empty = replace(params, instruction_parts=[])
        self.assertEqual(self.estimator.request(repeated, empty)[0],
                         self.estimator.request([replace(m, instructions=None) for m in repeated], empty)[0])
        self.assertGreater(self.estimator.request([], params)[0], self.estimator.request([], empty)[0])

    def test_unmeasured_image_supplement_but_old_image_is_not_recounted(self):
        image = BinaryContent(data=b"example", media_type="image/png")
        request = ModelRequest(parts=[UserPromptPart(["看图", image])])
        result = self.estimator.estimate_request([*self.history, request], self.params)
        self.assertEqual(result.tokens, 10100 + self.estimator.message(request))
        self.estimator.record_usage_anchor([request], self.params, self.response)
        self.assertEqual(self.estimator.request([request, self.response], self.params)[0], 10100)

    def test_same_length_changed_media_invalidates_baseline(self):
        request = ModelRequest(parts=[UserPromptPart([BinaryContent(b"abc", media_type="image/png")])])
        self.estimator.record_usage_anchor([request], self.params, self.response)
        changed = ModelRequest(parts=[UserPromptPart([BinaryContent(b"xyz", media_type="image/png")])])
        self.assertEqual(self.estimator.estimate_request([changed, self.response], self.params).fallback_reason, "history_changed")

    def test_business_metadata_and_timestamp_fields_are_fingerprinted(self):
        for field in ("metadata", "timestamp"):
            with self.subTest(field=field):
                request = ModelRequest(parts=[ToolReturnPart("read", {field: "short"}, "call")])
                self.estimator.record_usage_anchor([request], self.params, self.response)
                changed = ModelRequest(parts=[ToolReturnPart("read", {field: "x" * 10000}, "call")])
                self.assertEqual(self.estimator.estimate_request([changed, self.response], self.params).fallback_reason, "history_changed")
                params = replace(self.params, function_tools=[ToolDefinition(name="read", parameters_json_schema={
                    "type": "object", "properties": {field: {"type": "string"}},
                })])
                self.estimator.record_usage_anchor([self.request], params, self.response)
                changed_params = deepcopy(params)
                changed_params.function_tools[0].parameters_json_schema["properties"][field]["description"] = "x" * 10000
                self.assertEqual(self.estimator.estimate_request(self.history, changed_params).fallback_reason, "request_parameters_changed")

    def test_serialized_history_reopen_preserves_valid_baseline_and_usage(self):
        restored = [deserialize_message(serialize_message(m)) for m in self.history]
        result = self.estimator.estimate_request(restored, self.params)
        self.assertEqual(result.tokens, 10100)
        self.assertEqual(result.anchor_id, self.response.metadata[USAGE_ANCHOR_METADATA]["id"])
        self.assertEqual(restored[1].usage, self.response.usage)

    def test_invalid_baselines_fall_back_without_rewriting_usage(self):
        cases = [
            ([replace(self.request, parts=[UserPromptPart("压缩后的摘要")]), self.response], self.params, self.estimator, "history_changed"),
            (self.history, replace(self.params, function_tools=[ToolDefinition(name="new_tool")]), self.estimator, "request_parameters_changed"),
            (self.history, self.params, TokenEstimator.for_model("other", provider_type="private"), "model_changed"),
            (self.history, None, self.estimator, "request_parameters_unavailable"),
            ([self.request, replace(self.response, metadata=None)], self.params, self.estimator, "anchor_unverified"),
            ([*self.history, ModelResponse(parts=[TextPart("missing")])], self.params, self.estimator, "usage_unavailable"),
        ]
        for history, params, estimator, reason in cases:
            with self.subTest(reason=reason):
                original = deepcopy(history)
                result = estimator.estimate_request(history, params)
                self.assertEqual(result.source, "estimated")
                self.assertEqual(result.fallback_reason, reason)
                self.assertEqual(history, original)

    def test_fallback_instructions_change_invalidates_without_schema_change(self):
        params = ModelRequestParameters()
        self.estimator.record_usage_anchor([self.request], params, self.response)
        messages = [*self.history, ModelRequest(parts=[UserPromptPart("继续")], instructions="新的指令")]
        self.assertEqual(self.estimator.estimate_request(messages, params).fallback_reason, "instructions_changed")


class UsageAnchorSdkTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_tool_loop_stamps_anchor_and_final_budget(self):
        calls = []
        def model(messages, info):
            calls.append(messages)
            if len(calls) == 1:
                return ModelResponse(parts=[ToolCallPart("lookup", {}, "call-1")],
                                     usage=RequestUsage(input_tokens=10000, output_tokens=50))
            return ModelResponse(parts=[TextPart("完成")], usage=RequestUsage(input_tokens=11000, output_tokens=60))
        estimator = TokenEstimator.for_model("function", provider_type="test")
        capability = DeepAgentContextCompaction(context_window_tokens=100000, max_output_tokens=1000, estimator=estimator)
        state = ContextRunState(raw_history=[], estimator=estimator)
        agent = Agent(FunctionModel(model), capabilities=[capability], instructions="保持准确")
        @agent.tool_plain
        def lookup() -> str:
            return "事实：毛利130万元"
        result = await agent.run("查询", deps=SimpleNamespace(context_state=state))
        self.assertEqual(result.output, "完成")
        self.assertEqual(state.last_budget.context_occupancy_tokens, 11060)
        self.assertEqual(state.last_budget.occupancy_source, "provider_usage_plus_estimate")
        self.assertLess(state.telemetry.estimated_input_tokens, 10500)
        self.assertGreaterEqual(state.telemetry.estimated_input_tokens, 10050)
        self.assertTrue(all(USAGE_ANCHOR_METADATA in (m.metadata or {}) for m in result.all_messages() if isinstance(m, ModelResponse)))


if __name__ == "__main__":
    unittest.main()
