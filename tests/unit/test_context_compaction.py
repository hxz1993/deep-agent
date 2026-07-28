from __future__ import annotations

import unittest

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage, UsageLimits
from pydantic_ai_harness.compaction import estimate_token_count

from deep_agent.agent.context.budget import build_context_budget, predict_output_tokens
from deep_agent.agent.context.checkpoint import (
    deserialize_projection,
    history_hash,
    load_projection,
    projection_fingerprint,
    save_projection,
)
from deep_agent.agent.context.compaction import DeepAgentContextCompaction
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.runner import AgentRunner
from deep_agent.options import OPTION
from tests.helpers.sqlite_stores import make_sqlite_stores


def _long_tool_history(*, pairs: int = 8, result_chars: int = 1_200) -> list[ModelMessage]:
    history: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="请持续完成最初的任务")])
    ]
    for index in range(pairs):
        call_id = f"tool-{index}"
        history.extend(
            [
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            "search",
                            {"query": f"topic-{index}"},
                            call_id,
                        )
                    ]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            "search",
                            (
                                "Authorization: Bearer abcdefghij "
                                "api_key=plain-secret-token "
                                + f"result-{index}-"
                                + ("x" * result_chars)
                            ),
                            call_id,
                        )
                    ]
                ),
            ]
        )
    return history


class ContextBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = (
            OPTION.agent.context_compaction_enabled,
            OPTION.agent.context_compaction_trigger_ratio,
            OPTION.agent.context_compaction_target_ratio,
            OPTION.agent.context_output_token_floor,
        )
        OPTION.agent.context_compaction_enabled = True
        OPTION.agent.context_compaction_trigger_ratio = 0.80
        OPTION.agent.context_compaction_target_ratio = 0.55
        OPTION.agent.context_output_token_floor = 2_048

    def tearDown(self) -> None:
        (
            OPTION.agent.context_compaction_enabled,
            OPTION.agent.context_compaction_trigger_ratio,
            OPTION.agent.context_compaction_target_ratio,
            OPTION.agent.context_output_token_floor,
        ) = self._original

    def test_max_output_is_a_ceiling_not_the_reserved_output(self) -> None:
        budget = build_context_budget(
            [],
            context_window_tokens=1_000_000,
            max_output_tokens=384_000,
        )

        self.assertTrue(budget.enabled)
        self.assertEqual(budget.predicted_output_tokens, 2_048)
        self.assertEqual(budget.safety_margin_tokens, 50_000)
        self.assertEqual(budget.usable_request_tokens, 947_952)
        self.assertNotEqual(
            budget.usable_request_tokens,
            1_000_000 - 384_000 - 50_000,
        )

    def test_recent_usage_predicts_upper_quartile_bounded_by_model_maximum(
        self,
    ) -> None:
        messages = [
            ModelResponse(parts=[], usage=RequestUsage(output_tokens=value))
            for value in (600, 800, 1_000, 8_000)
        ]

        self.assertEqual(
            predict_output_tokens(
                messages,
                max_output_tokens=4_000,
                output_floor=512,
            ),
            4_000,
        )

    def test_unknown_context_window_disables_compaction(self) -> None:
        budget = build_context_budget(
            _long_tool_history(pairs=1),
            context_window_tokens=None,
            max_output_tokens=8_192,
        )

        self.assertFalse(budget.enabled)
        self.assertEqual(budget.reason, "context_window_unknown")
        self.assertIsNone(budget.trigger_tokens)


class ContextCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stores, self._engine, _ = await make_sqlite_stores()
        self._original = (
            OPTION.agent.context_compaction_enabled,
            OPTION.agent.context_compaction_trigger_ratio,
            OPTION.agent.context_compaction_target_ratio,
            OPTION.agent.context_output_token_floor,
            OPTION.agent.context_keep_tool_pairs,
            OPTION.agent.context_min_clear_tokens,
        )
        OPTION.agent.context_compaction_enabled = True
        OPTION.agent.context_compaction_trigger_ratio = 0.80
        OPTION.agent.context_compaction_target_ratio = 0.55
        OPTION.agent.context_output_token_floor = 128
        OPTION.agent.context_keep_tool_pairs = 3
        OPTION.agent.context_min_clear_tokens = 10

    async def asyncTearDown(self) -> None:
        (
            OPTION.agent.context_compaction_enabled,
            OPTION.agent.context_compaction_trigger_ratio,
            OPTION.agent.context_compaction_target_ratio,
            OPTION.agent.context_output_token_floor,
            OPTION.agent.context_keep_tool_pairs,
            OPTION.agent.context_min_clear_tokens,
        ) = self._original
        await self._engine.dispose()

    def _fingerprint(self) -> str:
        return projection_fingerprint(
            llm_model_id="main-model",
            context_window_tokens=2_000,
            trigger_ratio=OPTION.agent.context_compaction_trigger_ratio,
            target_ratio=OPTION.agent.context_compaction_target_ratio,
            keep_tool_pairs=OPTION.agent.context_keep_tool_pairs,
            summary_model_id="summary-model",
        )

    def _runner(self, capability: DeepAgentContextCompaction) -> AgentRunner:
        agent = Agent(
            TestModel(custom_output_text="done"),
            deps_type=AgentDeps,
            capabilities=[capability],
        )
        return AgentRunner(
            agent,
            self.stores.coordinator,
            UsageLimits(request_limit=20),
            context_window_tokens=2_000,
            max_output_tokens=500,
            context_compaction=capability,
            checkpoint_store=self.stores.context_checkpoint,
            checkpoint_fingerprint=self._fingerprint(),
            summary_model_id="summary-model",
        )

    async def test_harness_compacts_projection_without_rewriting_raw_history(
        self,
    ) -> None:
        raw_before = _long_tool_history()
        raw_before_tokens = estimate_token_count(raw_before)
        await self.stores.coordinator.append(
            "compact-session",
            raw_before,
            user_id="user-1",
        )
        capability = DeepAgentContextCompaction(
            context_window_tokens=2_000,
            max_output_tokens=500,
            summary_model=TestModel(
                custom_output_text=(
                    "## Intent\n继续完成用户最初的任务\n\n"
                    "## Current state\n旧工具结果已压缩"
                )
            ),
            summary_timeout=10,
        )
        runner = self._runner(capability)

        result = await runner.run(
            "compact-session",
            "继续",
            AgentDeps(session_id="compact-session", user="user-1"),
        )

        telemetry = result["context"]["compaction"]
        self.assertTrue(telemetry["triggered"])
        self.assertTrue(telemetry["summary_applied"])
        self.assertEqual(telemetry["strategy"], "tiered_summary")
        self.assertLess(
            telemetry["estimated_tokens_after"],
            telemetry["estimated_tokens_before"] * 0.50,
        )

        raw_after = await self.stores.coordinator.get_history("compact-session")
        self.assertEqual(len(raw_after), len(raw_before) + 2)
        raw_tool_returns = [
            part
            for message in raw_after
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        self.assertEqual(len(raw_tool_returns), 8)
        self.assertIn("Bearer abcdefghij", str(raw_tool_returns[0].content))
        self.assertGreater(raw_before_tokens, 2_000)

        checkpoint = await self.stores.context_checkpoint.get("compact-session")
        self.assertIsNotNone(checkpoint)
        assert checkpoint is not None
        self.assertEqual(checkpoint.row_version, 1)
        self.assertEqual(checkpoint.source_last_sequence, len(raw_after) - 1)
        self.assertLess(checkpoint.estimated_tokens, raw_before_tokens * 0.50)
        self.assertNotIn("Bearer abcdefghij", str(checkpoint.payload))
        self.assertNotIn("plain-secret-token", str(checkpoint.payload))

        projected = deserialize_projection(checkpoint.payload)
        summaries = [
            part.content
            for message in projected
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, SystemPromptPart)
        ]
        self.assertTrue(
            any(text.startswith("Summary of previous conversation:") for text in summaries)
        )
        call_ids = {
            part.tool_call_id
            for message in projected
            if isinstance(message, ModelResponse)
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        return_ids = {
            part.tool_call_id
            for message in projected
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        }
        self.assertTrue(return_ids.issubset(call_ids))

        await runner.run(
            "compact-session",
            "再继续一步",
            AgentDeps(session_id="compact-session", user="user-1"),
        )
        unchanged = await self.stores.context_checkpoint.get("compact-session")
        self.assertIsNotNone(unchanged)
        assert unchanged is not None
        self.assertEqual(unchanged.row_version, 1)
        self.assertEqual(
            unchanged.source_last_sequence,
            checkpoint.source_last_sequence,
        )

    async def test_checkpoint_cas_hash_and_branch_invalidation(self) -> None:
        raw = [
            ModelRequest(parts=[UserPromptPart(content="raw-a")]),
            ModelResponse(parts=[]),
        ]
        projected = [ModelRequest(parts=[UserPromptPart(content="projection-a")])]
        fingerprint = self._fingerprint()

        self.assertTrue(
            await save_projection(
                self.stores.context_checkpoint,
                raw_history=raw,
                projected_history=projected,
                session_id="cas-session",
                agent_id=None,
                fingerprint=fingerprint,
                expected_row_version=0,
                summary_model_id="summary-model",
            )
        )
        self.assertFalse(
            await save_projection(
                self.stores.context_checkpoint,
                raw_history=raw,
                projected_history=projected,
                session_id="cas-session",
                agent_id=None,
                fingerprint=fingerprint,
                expected_row_version=0,
                summary_model_id="summary-model",
            )
        )
        await self.stores.coordinator.append("cas-session", raw)

        valid = await load_projection(
            self.stores.context_checkpoint,
            raw,
            session_id="cas-session",
            agent_id=None,
            fingerprint=fingerprint,
        )
        self.assertTrue(valid.used_checkpoint)
        self.assertEqual(valid.messages, projected)

        mismatched = await load_projection(
            self.stores.context_checkpoint,
            raw,
            session_id="cas-session",
            agent_id=None,
            fingerprint="different-policy",
        )
        self.assertFalse(mismatched.used_checkpoint)
        self.assertEqual(mismatched.invalid_reason, "projection_fingerprint")
        self.assertEqual(history_hash(mismatched.messages), history_hash(raw))

        await self.stores.coordinator.truncate_history(
            "cas-session",
            keep_count=1,
        )
        self.assertIsNone(
            await self.stores.context_checkpoint.get("cas-session")
        )

    async def test_summary_failure_uses_fallback_without_writing_checkpoint(
        self,
    ) -> None:
        async def fail_summary(*_args, **_kwargs):
            raise RuntimeError("summary unavailable")

        raw_before = _long_tool_history()
        await self.stores.coordinator.append(
            "summary-failure",
            raw_before,
            user_id="user-1",
        )
        capability = DeepAgentContextCompaction(
            context_window_tokens=2_000,
            max_output_tokens=500,
            summary_model=FunctionModel(fail_summary),
            summary_timeout=10,
        )

        result = await self._runner(capability).run(
            "summary-failure",
            "继续",
            AgentDeps(session_id="summary-failure", user="user-1"),
        )

        telemetry = result["context"]["compaction"]
        self.assertTrue(telemetry["triggered"])
        self.assertTrue(telemetry["summary_failed"])
        self.assertFalse(telemetry["summary_applied"])
        self.assertEqual(telemetry["strategy"], "sliding_window_fallback")
        self.assertIsNone(
            await self.stores.context_checkpoint.get("summary-failure")
        )
        self.assertEqual(
            len(await self.stores.coordinator.get_history("summary-failure")),
            len(raw_before) + 2,
        )


if __name__ == "__main__":
    unittest.main()
