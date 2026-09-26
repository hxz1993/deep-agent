"""Usage anchors survive the real SDK → incremental Event Store → reopen path."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import unittest
import time
from unittest.mock import patch

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import InstructionPart, ModelRequest, ModelResponse, TextPart, ThinkingPart, ToolCallPart, UserPromptPart
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage, RunUsage, UsageLimits

from deep_agent.agent.context.compaction import DeepAgentContextCompaction
from deep_agent.agent.context.estimator import TokenEstimator, USAGE_ANCHOR_METADATA
from deep_agent.agent.context.runtime import ContextRunState
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.runner import AgentRunner
from deep_agent.diagnostics.sessions import detail_event_metadata
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent
from tests.unit.test_context_compaction import valid_summary


class ContextUsagePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        stores, self.engine, _ = await make_sqlite_stores()
        self.store = stores.coordinator
        self.agent_id = await seed_default_agent(stores)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_incremental_response_anchor_is_valid_after_history_reopen(self):
        requests, parameters, before_budgets = [], [], []
        estimator = TokenEstimator.for_model("usage-fixture", provider_type="test")

        class ObservedCompaction(DeepAgentContextCompaction):
            async def before_model_request(self, ctx, request_context):
                result = await super().before_model_request(ctx, request_context)
                parameters.append(deepcopy(result.model_request_parameters))
                before_budgets.append(ctx.deps.context_state.last_budget)
                return result

        capability = ObservedCompaction(context_window_tokens=100000, max_output_tokens=1000,
            estimator=estimator)

        def respond(messages, info):
            requests.append(deepcopy(messages))
            if len(requests) == 1:
                return ModelResponse(parts=[ThinkingPart("查验数据"), ToolCallPart("lookup", {}, "lookup-1")],
                    usage=RequestUsage(input_tokens=10000, output_tokens=50))
            return ModelResponse(parts=[ThinkingPart("整理证据"), TextPart("毛利130万元")],
                usage=RequestUsage(input_tokens=11000, output_tokens=60))

        agent = Agent(FunctionModel(respond), deps_type=AgentDeps, capabilities=[capability],
            instructions="只依据工具证据作答")

        @agent.tool_plain
        async def lookup() -> str:
            # A real independent store read while the SDK tool executes proves
            # the anchor was saved by incremental persistence, not terminal repair.
            saved = await self.store.get_raw_history("usage-session", agent_id=self.agent_id)
            responses = [message for message in saved if isinstance(message, ModelResponse)]
            self.assertEqual(len(responses), 1)
            self.assertIn(USAGE_ANCHOR_METADATA, responses[0].metadata)
            self.assertEqual(responses[0].usage.input_tokens, 10000)
            return "收入360万元，成本230万元，毛利130万元。"

        runner = AgentRunner(agent, self.store, UsageLimits(request_limit=10),
            context_window_tokens=100000, max_output_tokens=1000,
            context_compaction=capability, estimator=estimator, model_id="usage-fixture")
        def deps():
            return AgentDeps(session_id="usage-session", user="usage-owner", agent_id=self.agent_id)

        with patch.object(AgentRunner, "_generate_model_title", return_value=None):
            result = await runner.run("usage-session", "请查询毛利", deps())
            self.assertEqual(result["status"], "completed")
            self.assertEqual(before_budgets[0].occupancy_source, "estimated")
            self.assertEqual(before_budgets[1].occupancy_source, "provider_usage_plus_estimate")
            self.assertGreaterEqual(before_budgets[1].context_occupancy_tokens, 10050)
            self.assertLess(before_budgets[1].context_occupancy_tokens, 11000)

            restored = await self.store.get_history("usage-session", agent_id=self.agent_id)
            responses = [message for message in restored if isinstance(message, ModelResponse)]
            self.assertEqual(len(responses), 2)
            anchors = [message.metadata[USAGE_ANCHOR_METADATA]["id"] for message in responses]
            self.assertEqual(len(set(anchors)), 2)
            estimate = estimator.estimate_request(restored, parameters[-1])
            self.assertEqual(estimate.source, "provider_usage_plus_estimate")
            self.assertEqual(estimate.anchor_id, anchors[-1])
            self.assertEqual(estimate.tokens, 11060)

            result = await runner.run("usage-session", "复述刚才的毛利", deps())
            self.assertEqual(result["status"], "completed")
            self.assertEqual(before_budgets[2].occupancy_source, "provider_usage_plus_estimate")
            self.assertEqual(before_budgets[2].usage_anchor_id, anchors[-1])
            self.assertGreater(before_budgets[2].context_occupancy_tokens, 11060)

        # Three SDK responses, each split into two Events, remain three usages.
        metadata = detail_event_metadata(await self.store.list_all_events("usage-session"))
        self.assertEqual(metadata["usage"]["input_tokens"], 32000)
        self.assertEqual(metadata["usage"]["output_tokens"], 170)
        self.assertEqual(sum(run["recorded_model_responses"] for run in metadata["runs"]), 3)

    async def test_pressure_and_manual_use_same_projection_and_real_summary_path(self):
        raw = [ModelRequest(parts=[UserPromptPart("历史资料" * 2900)]),
            ModelResponse(parts=[TextPart("历史分析" * 2900)]),
            ModelRequest(parts=[UserPromptPart("继续保留任务")])]
        estimator = TokenEstimator.for_model("usage-fixture", provider_type="test")
        outcomes = []
        for trigger in ("manual", "pressure"):
            with self.subTest(trigger=trigger):
                summary_responses, shared_calls = [], []

                def summarize(messages, info):
                    response = ModelResponse(parts=[TextPart(valid_summary())],
                        usage=RequestUsage(input_tokens=7000, output_tokens=150))
                    summary_responses.append(response)
                    return response

                class ObservedCompaction(DeepAgentContextCompaction):
                    async def _compact_projection(self, *args, **kwargs):
                        shared_calls.append(kwargs)
                        return await super()._compact_projection(*args, **kwargs)

                capability = ObservedCompaction(context_window_tokens=30000, max_output_tokens=1000,
                    estimator=estimator, summary_timeout=5)
                state = ContextRunState(raw_history=deepcopy(raw), estimator=estimator,
                    usage_limits=UsageLimits(request_limit=10))
                deps = AgentDeps(session_id=f"summary-{trigger}", context_state=state)
                model = FunctionModel(summarize)
                ctx = RunContext(deps=deps, model=model, usage=RunUsage(),
                    usage_limits=state.usage_limits)
                request = ModelRequestContext(model=model, messages=deepcopy(raw),
                    model_settings=None, model_request_parameters=ModelRequestParameters())
                if trigger == "manual":
                    projected = await capability.compact_manually(ctx, request,
                        state=state, budget_limit_tokens=14000)
                else:
                    projected = list((await capability.before_model_request(ctx, request)).messages)
                self.assertEqual(len(shared_calls), 1)
                self.assertEqual(len(summary_responses), 1)
                self.assertTrue(state.telemetry.candidate_valid)
                self.assertTrue(state.telemetry.summary_applied)
                self.assertEqual(projected[-1], raw[-1])
                self.assertLess(estimator.estimate_request(projected, request.model_request_parameters).tokens,
                    estimator.estimate_request(raw, request.model_request_parameters).tokens)
                self.assertNotIn(USAGE_ANCHOR_METADATA, summary_responses[0].metadata or {})
                self.assertEqual(state.raw_history, raw)
                # Later model requests must not change the committed operation's
                # before count or parameter set; persisted evidence is re-counted.
                before = state.compaction_estimated_tokens_before
                compaction_parameters = deepcopy(state.compaction_request_parameters)
                state.telemetry.estimated_tokens_before = 123
                request.model_request_parameters.instruction_parts = [InstructionPart(content="新指令" * 20000)]
                session_id = f"persisted-{trigger}"
                await self.store.append(session_id, raw, agent_id=self.agent_id, user_id="usage-owner",
                    turn_id="seed", run_id="seed")
                deps.agent_id, deps.user = self.agent_id, "usage-owner"
                runner = AgentRunner(Agent(model), self.store, UsageLimits(request_limit=10),
                    estimator=estimator, context_window_tokens=30000, max_output_tokens=1000,
                    context_compaction=capability)
                target = 14000 if trigger == "manual" else state.last_budget.usable_request_tokens
                if trigger == "pressure":
                    state.last_budget = replace(state.last_budget, usable_request_tokens=1)
                    saved = await runner._maybe_save_compaction(session_id, deps,
                        projected_history=projected)
                else:
                    saved = await runner._persist_compaction(session_id, deps, raw_history=raw,
                        projected_history=projected, trigger=trigger, started_at=time.perf_counter(),
                        budget_limit_tokens=target)
                self.assertEqual(saved.status, "completed")
                persisted = await self.store.get_history(session_id, agent_id=self.agent_id)
                budget = saved.payload["budget"]
                self.assertEqual(budget["estimated_before"], estimator.messages(raw))
                self.assertEqual(budget["estimated_after"], estimator.messages(persisted))
                self.assertEqual(budget["estimated_tokens_before"], before)
                self.assertNotEqual(budget["estimated_tokens_before"], 123)
                self.assertEqual(budget["estimated_tokens_after"],
                    estimator.estimate_request(persisted, compaction_parameters).tokens)
                self.assertEqual(budget["estimated_tokens_after"], state.telemetry.estimated_tokens_after)
                self.assertLessEqual(budget["estimated_tokens_after"], target)
                self.assertEqual(budget["budget_limit_tokens"], target)
                self.assertEqual(budget["estimate_scope"],
                    "full_model_request" if trigger == "pressure" else "history_only")
                outcomes.append(projected)
        self.assertEqual(len(outcomes), 2)
        self.assertEqual([message.parts[0].content for message in outcomes[0]],
            [message.parts[0].content for message in outcomes[1]])


if __name__ == "__main__":
    unittest.main()
