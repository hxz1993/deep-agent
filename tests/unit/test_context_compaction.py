from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.function import AgentInfo
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RequestUsage, RunUsage, UsageLimits
from pydantic_ai_harness.compaction import estimate_token_count

from deep_agent.agent.context.budget import build_context_budget, predict_output_tokens
from deep_agent.agent.attachments import attachment_ref_dict
from deep_agent.agent.context.checkpoint import (
    deserialize_projection,
    history_hash,
    load_projection,
    projection_fingerprint,
    save_projection,
)
from deep_agent.agent.context.compaction import (
    ContextWindowExceededError,
    DeepAgentContextCompaction,
    SummaryUnavailable,
    _is_transient_summary_error,
    _logical_units,
    _summary_prompt,
    _summary_safety_tokens,
    _strip_summary_and_system,
)
from deep_agent.agent.context_references import context_reference_dict
from deep_agent.agent.context.estimator import (
    TokenEstimator,
    conservative_text_tokens,
)
from deep_agent.agent.context.test_scenarios import (
    CONTEXT_TEST_SCENARIOS,
    build_context_test_history,
)
from deep_agent.agent.context.file_keys import deep_agent_file_key
from deep_agent.agent.context.runtime import ContextRunState
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.runner import AgentRunner
from deep_agent.db.serialization import deserialize_message, serialize_message
from deep_agent.options import OPTION
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.schemas.context_reference import ContextReference
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


class AttachmentFileKeyTests(unittest.TestCase):
    def test_same_attachment_range_has_same_key(self) -> None:
        first = ToolCallPart(
            "read_text_attachment",
            {"file_id": "file-1", "offset": 1, "limit": 500},
            "call-1",
        )
        second = ToolCallPart(
            "read_text_attachment",
            {"file_id": "file-1", "offset": 1, "limit": 500},
            "call-2",
        )
        self.assertEqual(deep_agent_file_key(first), deep_agent_file_key(second))

    def test_different_attachment_pages_and_sheets_are_not_deduplicated(self) -> None:
        page_one = ToolCallPart(
            "read_xlsx",
            {"file_id": "file-1", "sheet": "A", "offset": 1, "limit": 100},
            "call-1",
        )
        page_two = ToolCallPart(
            "read_xlsx",
            {"file_id": "file-1", "sheet": "A", "offset": 101, "limit": 100},
            "call-2",
        )
        other_sheet = ToolCallPart(
            "read_xlsx",
            {"file_id": "file-1", "sheet": "B", "offset": 1, "limit": 100},
            "call-3",
        )
        self.assertNotEqual(
            deep_agent_file_key(page_one),
            deep_agent_file_key(page_two),
        )
        self.assertNotEqual(
            deep_agent_file_key(page_one),
            deep_agent_file_key(other_sheet),
        )


class ContextBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = (
            OPTION.agent.context_compaction_trigger_ratio,
            OPTION.agent.context_compaction_target_ratio,
        )
        OPTION.agent.context_compaction_trigger_ratio = 0.80
        OPTION.agent.context_compaction_target_ratio = 0.55

    def tearDown(self) -> None:
        (
            OPTION.agent.context_compaction_trigger_ratio,
            OPTION.agent.context_compaction_target_ratio,
        ) = self._original

    def test_max_output_is_a_ceiling_not_the_reserved_output(self) -> None:
        budget = build_context_budget(
            [],
            context_window_tokens=1_000_000,
            max_output_tokens=384_000,
        )

        self.assertTrue(budget.enabled)
        self.assertEqual(budget.predicted_output_tokens, 2_048)
        self.assertEqual(budget.safety_margin_tokens, 1_024)
        self.assertEqual(budget.usable_request_tokens, 996_928)
        self.assertNotEqual(
            budget.usable_request_tokens,
            1_000_000 - 384_000 - 1_024,
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
            2_048,
        )

    def test_missing_usage_uses_estimated_output_sample(self) -> None:
        messages = [ModelResponse(parts=[], usage=RequestUsage())]
        budget = build_context_budget(
            messages,
            context_window_tokens=16_000,
            max_output_tokens=8_000,
        )
        self.assertEqual(budget.output_prediction_source, "estimated")
        self.assertEqual(budget.estimated_sample_count, 1)

    def test_conservative_estimator_is_cjk_and_symbol_sensitive(self) -> None:
        self.assertGreaterEqual(conservative_text_tokens("中文测试"), 4)
        self.assertGreater(
            conservative_text_tokens('{"a":[1,2,3]}'),
            conservative_text_tokens("abcdefghijkl"),
        )

    def test_private_openai_compatible_model_does_not_use_tiktoken(self) -> None:
        estimator = TokenEstimator.for_model(
            "openai:gpt-4o",
            "https://private.example/v1",
        )
        self.assertEqual(estimator.source, "conservative_fallback")

    def test_unknown_context_window_disables_compaction(self) -> None:
        budget = build_context_budget(
            _long_tool_history(pairs=1),
            context_window_tokens=None,
            max_output_tokens=8_192,
        )

        self.assertFalse(budget.enabled)
        self.assertEqual(budget.reason, "context_window_unknown")
        self.assertIsNone(budget.trigger_tokens)


class ContextCompactionAlgorithmTests(unittest.TestCase):
    def test_summary_safety_uses_estimator_specific_margin(self) -> None:
        conservative = TokenEstimator.conservative()
        tokenizer = TokenEstimator(
            source="model_tokenizer",
            _counter=lambda text: conservative.text(text),
        )
        self.assertEqual(_summary_safety_tokens(conservative, 20_000), 2_000)
        self.assertEqual(_summary_safety_tokens(tokenizer, 20_000), 1_024)

    def test_only_transient_provider_errors_are_retried(self) -> None:
        class ProviderError(RuntimeError):
            def __init__(self, status_code: int) -> None:
                self.status_code = status_code
                super().__init__(f"HTTP {status_code}")

        self.assertTrue(_is_transient_summary_error(ProviderError(503)))
        self.assertTrue(_is_transient_summary_error(TimeoutError("timed out")))
        self.assertFalse(_is_transient_summary_error(ProviderError(401)))
        self.assertFalse(_is_transient_summary_error(ValueError("bad schema")))

    def test_old_summary_appears_once_in_incremental_summary_prompt(self) -> None:
        old_summary = "旧摘要唯一标记"
        first_user = ModelRequest(parts=[UserPromptPart(content="最初任务")])
        messages = [
            ModelRequest(
                parts=[
                    SystemPromptPart(
                        content=f"Summary of previous conversation:\n\n{old_summary}"
                    )
                ]
            ),
            first_user,
            ModelResponse(parts=[TextPart(content="旧回答")]),
            ModelRequest(parts=[UserPromptPart(content="新增问题")]),
        ]
        to_summarize = _strip_summary_and_system(
            messages,
            first_user=first_user,
        )
        prompt = _summary_prompt(to_summarize, old_summary)

        self.assertEqual(prompt.count(old_summary), 1)
        self.assertNotIn("最初任务", prompt)
        self.assertIn("旧回答", prompt)
        self.assertIn("新增问题", prompt)

    def test_summary_units_keep_tool_call_and_result_together(self) -> None:
        messages = [
            ModelRequest(parts=[UserPromptPart(content="查询")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "search",
                        {"query": "context"},
                        "paired-call",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        "search",
                        "result",
                        "paired-call",
                    )
                ]
            ),
            ModelResponse(parts=[TextPart(content="完成")]),
        ]

        units = _logical_units(messages)

        paired_unit = next(
            unit
            for unit in units
            if any(
                isinstance(part, ToolCallPart)
                for message in unit
                for part in message.parts
            )
        )
        self.assertEqual(len(paired_unit), 2)
        self.assertTrue(
            any(
                isinstance(part, ToolReturnPart)
                for message in paired_unit
                for part in message.parts
            )
        )


class ContextCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stores, self._engine, _ = await make_sqlite_stores()
        self._original = (
            OPTION.agent.context_compaction_trigger_ratio,
            OPTION.agent.context_compaction_target_ratio,
            OPTION.agent.context_keep_tool_pairs,
            OPTION.agent.context_min_clear_tokens,
        )
        OPTION.agent.context_compaction_trigger_ratio = 0.80
        OPTION.agent.context_compaction_target_ratio = 0.55
        OPTION.agent.context_keep_tool_pairs = 3
        OPTION.agent.context_min_clear_tokens = 10
        self.persister = EventPersister(self.stores.coordinator)
        await self.persister.start()

    async def asyncTearDown(self) -> None:
        (
            OPTION.agent.context_compaction_trigger_ratio,
            OPTION.agent.context_compaction_target_ratio,
            OPTION.agent.context_keep_tool_pairs,
            OPTION.agent.context_min_clear_tokens,
        ) = self._original
        await self.persister.aclose()
        await self._engine.dispose()

    def _fingerprint(self) -> str:
        return projection_fingerprint(
            llm_model_id="main-model",
            context_window_tokens=4_000,
            trigger_ratio=OPTION.agent.context_compaction_trigger_ratio,
            target_ratio=OPTION.agent.context_compaction_target_ratio,
            keep_tool_pairs=OPTION.agent.context_keep_tool_pairs,
            summary_model_id="summary-model",
        )

    def _runner(
        self,
        capability: DeepAgentContextCompaction,
        *,
        model=None,
    ) -> AgentRunner:
        agent = Agent(
            model or TestModel(call_tools=[], custom_output_text="done"),
            deps_type=AgentDeps,
            capabilities=[capability],
        )
        return AgentRunner(
            agent,
            self.stores.coordinator,
            UsageLimits(request_limit=20),
            context_window_tokens=4_000,
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
            context_window_tokens=4_000,
            max_output_tokens=500,
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
        self.assertTrue(telemetry["summary_applied"], telemetry)
        self.assertTrue(telemetry["checkpoint_saved"], telemetry)
        self.assertEqual(telemetry["strategy"], "tiered_summary")
        self.assertLess(
            telemetry["estimated_tokens_after"],
            telemetry["estimated_tokens_before"] * 0.50,
        )
        self.assertEqual(
            telemetry["estimated_input_tokens"],
            telemetry["estimated_tokens_after"],
        )
        self.assertGreaterEqual(result["usage"]["requests"], 2)

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

    async def test_automatic_compaction_projects_context_refs_without_metadata(
        self,
    ) -> None:
        ref = ContextReference(
            key="selection",
            type="deeptable.selection",
            schema_version="2.0",
            label="预算!B2:C4",
            payload={"cells": [{"address": "B2", "value": 42}]},
            metadata={"history_only_secret": "must-stay-out-of-checkpoint"},
        )
        raw = _long_tool_history()
        first = raw[0]
        assert isinstance(first, ModelRequest)
        raw[0] = ModelRequest(
            parts=[
                UserPromptPart(
                    content=[
                        context_reference_dict(ref),
                        "请持续完成最初的任务",
                    ]
                )
            ]
        )
        await self.stores.coordinator.append(
            "context-ref-auto-compact",
            raw,
            user_id="user-1",
        )
        capability = DeepAgentContextCompaction(
            context_window_tokens=4_000,
            max_output_tokens=500,
            summary_timeout=10,
        )

        result = await self._runner(capability).run(
            "context-ref-auto-compact",
            "继续",
            AgentDeps(session_id="context-ref-auto-compact", user="user-1"),
        )

        self.assertTrue(result["context"]["compaction"]["summary_applied"])
        stored = await self.stores.coordinator.get_history("context-ref-auto-compact")
        self.assertIn("must-stay-out-of-checkpoint", str(serialize_message(stored[0])))
        checkpoint = await self.stores.context_checkpoint.get(
            "context-ref-auto-compact"
        )
        self.assertIsNotNone(checkpoint)
        assert checkpoint is not None
        self.assertNotIn("must-stay-out-of-checkpoint", str(checkpoint.payload))

    async def test_large_window_history_uses_one_summary_then_reuses_checkpoint(
        self,
    ) -> None:
        context_window = 40_000
        max_output = 4_000
        estimator = TokenEstimator.conservative()
        built = build_context_test_history(
            CONTEXT_TEST_SCENARIOS[0],
            context_window_tokens=context_window,
            max_output_tokens=max_output,
            estimator=estimator,
        )
        await self.stores.coordinator.append(
            "large-window-single-summary",
            built.messages,
            user_id="user-1",
        )
        summary_prompt_tokens: list[int] = []

        def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
            if "<history>" in str(messages):
                prompt_tokens = sum(
                    estimator.text(str(getattr(part, "content", "")))
                    for message in messages
                    for part in message.parts
                )
                summary_prompt_tokens.append(prompt_tokens)
                return ModelResponse(parts=[TextPart(content="单次摘要：保留目标与约束。")])
            return ModelResponse(parts=[TextPart(content="done")])

        capability = DeepAgentContextCompaction(
            context_window_tokens=context_window,
            max_output_tokens=max_output,
            summary_timeout=10,
        )
        fingerprint = projection_fingerprint(
            llm_model_id="main-model",
            context_window_tokens=context_window,
            max_output_tokens=max_output,
            trigger_ratio=OPTION.agent.context_compaction_trigger_ratio,
            target_ratio=OPTION.agent.context_compaction_target_ratio,
            keep_tool_pairs=OPTION.agent.context_keep_tool_pairs,
            summary_model_id="main-model",
            estimator_source=estimator.source,
        )
        runner = AgentRunner(
            Agent(
                FunctionModel(respond),
                deps_type=AgentDeps,
                capabilities=[capability],
            ),
            self.stores.coordinator,
            UsageLimits(request_limit=20),
            context_window_tokens=context_window,
            max_output_tokens=max_output,
            context_compaction=capability,
            checkpoint_store=self.stores.context_checkpoint,
            checkpoint_fingerprint=fingerprint,
            summary_model_id="main-model",
            estimator=estimator,
        )

        first = await runner.run(
            "large-window-single-summary",
            "总结并继续",
            AgentDeps(session_id="large-window-rolling-summary", user="user-1"),
        )
        self.assertEqual(first["context"]["compaction"]["summary_calls"], 1)
        self.assertEqual(first["context"]["compaction"]["summary_chunks"], 1)
        self.assertEqual(len(summary_prompt_tokens), 1)
        prompt_tokens = summary_prompt_tokens[0]
        output_limit = min(
            max_output,
            4_096,
            max(256, int(first["context"]["budget"]["target_tokens"] * 0.10)),
        )
        self.assertLessEqual(
            prompt_tokens
            + output_limit
            + _summary_safety_tokens(estimator, prompt_tokens),
            context_window,
        )
        checkpoint = await self.stores.context_checkpoint.get(
            "large-window-single-summary"
        )
        self.assertIsNotNone(checkpoint)

        second = await runner.run(
            "large-window-single-summary",
            "继续",
            AgentDeps(session_id="large-window-rolling-summary", user="user-1"),
        )
        self.assertFalse(second["context"]["compaction"]["triggered"])
        unchanged = await self.stores.context_checkpoint.get(
            "large-window-single-summary"
        )
        self.assertEqual(unchanged.row_version, checkpoint.row_version)

    async def test_transient_summary_failure_retries_the_identical_prompt_once(
        self,
    ) -> None:
        capability = DeepAgentContextCompaction(
            context_window_tokens=8_000,
            max_output_tokens=500,
            summary_timeout=10,
        )
        state = ContextRunState(
            raw_history=[],
            estimator=TokenEstimator.conservative(),
            usage_limits=UsageLimits(request_limit=5),
        )
        context = RunContext(
            deps=None,
            model=TestModel(),
            usage=RunUsage(),
        )
        messages = [
            ModelResponse(parts=[TextPart(content="需要压缩的旧历史")]),
            ModelRequest(parts=[UserPromptPart(content="继续")]),
        ]

        with (
            patch(
                "deep_agent.agent.context.compaction.Agent.run",
                new=AsyncMock(
                    side_effect=[
                        TimeoutError("provider timed out"),
                        SimpleNamespace(output="重试后摘要"),
                    ]
                ),
            ) as run,
            patch(
                "deep_agent.agent.context.compaction.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep,
        ):
            summary = await capability._single_summary(
                messages,
                None,
                context,
                500,
                state,
                reserve_answer_request=True,
            )

        self.assertEqual(summary, "重试后摘要")
        self.assertEqual(run.await_count, 2)
        self.assertEqual(
            run.await_args_list[0].args[0],
            run.await_args_list[1].args[0],
        )
        sleep.assert_awaited_once_with(1.0)
        self.assertEqual(state.telemetry.summary_calls, 2)
        self.assertEqual(state.telemetry.summary_chunks, 1)

    async def test_two_transient_summary_failures_stop_after_two_identical_calls(
        self,
    ) -> None:
        capability = DeepAgentContextCompaction(
            context_window_tokens=8_000,
            max_output_tokens=500,
            summary_timeout=10,
        )
        state = ContextRunState(
            raw_history=[],
            estimator=TokenEstimator.conservative(),
            usage_limits=UsageLimits(request_limit=5),
        )
        context = RunContext(deps=None, model=TestModel(), usage=RunUsage())
        run = AsyncMock(
            side_effect=[
                TimeoutError("first timeout"),
                TimeoutError("second timeout"),
            ]
        )
        with (
            patch("deep_agent.agent.context.compaction.Agent.run", new=run),
            patch(
                "deep_agent.agent.context.compaction.asyncio.sleep",
                new=AsyncMock(),
            ),
        ):
            with self.assertRaises(SummaryUnavailable) as raised:
                await capability._single_summary(
                    [ModelResponse(parts=[TextPart(content="旧历史")])],
                    None,
                    context,
                    500,
                    state,
                    reserve_answer_request=True,
                )

        self.assertEqual(raised.exception.reason, "summary_timeout")
        self.assertEqual(run.await_count, 2)
        self.assertEqual(run.await_args_list[0].args[0], run.await_args_list[1].args[0])
        self.assertEqual(state.telemetry.summary_calls, 2)
        self.assertEqual(state.telemetry.summary_chunks, 1)

    async def test_automatic_summary_reserves_one_request_for_the_answer(self) -> None:
        capability = DeepAgentContextCompaction(
            context_window_tokens=8_000,
            max_output_tokens=500,
            summary_timeout=10,
        )
        state = ContextRunState(
            raw_history=[],
            estimator=TokenEstimator.conservative(),
            usage_limits=UsageLimits(request_limit=1),
        )
        context = RunContext(deps=None, model=TestModel(), usage=RunUsage())
        with patch(
            "deep_agent.agent.context.compaction.Agent.run",
            new=AsyncMock(),
        ) as run:
            with self.assertRaises(SummaryUnavailable) as raised:
                await capability._single_summary(
                    [ModelResponse(parts=[TextPart(content="旧历史")])],
                    None,
                    context,
                    500,
                    state,
                    reserve_answer_request=True,
                )

        self.assertEqual(raised.exception.reason, "request_limit_reserved")
        run.assert_not_awaited()

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
        def fail_summary(
            messages: list[ModelMessage],
            _info: AgentInfo,
        ) -> ModelResponse:
            if "<history>" in str(messages):
                raise RuntimeError("summary unavailable")
            return ModelResponse(parts=[TextPart(content="done")])

        raw_before = _long_tool_history()
        await self.stores.coordinator.append(
            "summary-failure",
            raw_before,
            user_id="user-1",
        )
        capability = DeepAgentContextCompaction(
            context_window_tokens=4_000,
            max_output_tokens=500,
            summary_timeout=10,
        )

        result = await self._runner(
            capability,
            model=FunctionModel(fail_summary),
        ).run(
            "summary-failure",
            "继续",
            AgentDeps(session_id="summary-failure", user="user-1"),
        )

        telemetry = result["context"]["compaction"]
        self.assertTrue(telemetry["triggered"])
        self.assertTrue(telemetry["summary_failed"])
        self.assertFalse(telemetry["summary_applied"])
        self.assertIsNone(telemetry["checkpoint_saved"])
        self.assertEqual(telemetry["strategy"], "sliding_window_fallback")
        self.assertIsNone(
            await self.stores.context_checkpoint.get("summary-failure")
        )
        self.assertEqual(
            len(await self.stores.coordinator.get_history("summary-failure")),
            len(raw_before) + 2,
        )

    async def test_no_single_request_intersection_uses_sliding_without_summary_call(
        self,
    ) -> None:
        summary_calls = 0

        def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
            nonlocal summary_calls
            if "<history>" in str(messages):
                summary_calls += 1
            return ModelResponse(parts=[TextPart(content="done")])

        raw = [
            ModelRequest(parts=[UserPromptPart(content="首条任务" * 220)]),
            ModelResponse(parts=[TextPart(content="早期结论" * 220)]),
            ModelRequest(parts=[UserPromptPart(content="中段约束" * 220)]),
            ModelResponse(parts=[TextPart(content="中段结论" * 220)]),
        ]
        await self.stores.coordinator.append(
            "no-safe-intersection-auto",
            raw,
            user_id="user-1",
        )
        capability = DeepAgentContextCompaction(
            context_window_tokens=4_000,
            max_output_tokens=500,
            summary_timeout=10,
        )

        result = await self._runner(
            capability,
            model=FunctionModel(respond),
        ).run(
            "no-safe-intersection-auto",
            "当前问题" * 220,
            AgentDeps(session_id="no-safe-intersection-auto", user="user-1"),
        )

        telemetry = result["context"]["compaction"]
        self.assertEqual(summary_calls, 0)
        self.assertEqual(telemetry["strategy"], "sliding_window_fallback")
        self.assertFalse(telemetry["summary_applied"])
        self.assertIsNone(
            await self.stores.context_checkpoint.get("no-safe-intersection-auto")
        )

    async def test_manual_input_too_large_does_not_call_model_or_write_checkpoint(
        self,
    ) -> None:
        summary_calls = 0

        def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
            nonlocal summary_calls
            if "<history>" in str(messages):
                summary_calls += 1
            return ModelResponse(parts=[TextPart(content="unused")])

        raw = [
            ModelRequest(parts=[UserPromptPart(content="首条任务" * 220)]),
            ModelResponse(parts=[TextPart(content="早期结论" * 220)]),
            ModelRequest(parts=[UserPromptPart(content="当前问题" * 220)]),
            ModelResponse(parts=[TextPart(content="当前结论" * 220)]),
        ]
        await self.stores.coordinator.append(
            "no-safe-intersection-manual",
            raw,
            user_id="user-1",
        )
        capability = DeepAgentContextCompaction(
            context_window_tokens=4_000,
            max_output_tokens=500,
            summary_timeout=10,
        )
        events = [
            event
            async for event in self._runner(
                capability,
                model=FunctionModel(respond),
            ).run_manual_compaction(
                "no-safe-intersection-manual",
                AgentDeps(session_id="no-safe-intersection-manual", user="user-1"),
            )
        ]

        self.assertEqual(events[-1]["status"], "failed")
        self.assertEqual(events[-1]["reason"], "summary_input_too_large")
        self.assertEqual(summary_calls, 0)
        self.assertIsNone(
            await self.stores.context_checkpoint.get("no-safe-intersection-manual")
        )

    async def test_attachment_scope_comes_from_raw_history_not_checkpoint_payload(
        self,
    ) -> None:
        session_id = "attachment-checkpoint"
        raw = [
            ModelRequest(
                parts=[
                    UserPromptPart(
                        content=[
                            "读取附件",
                            attachment_ref_dict(
                                AttachmentRef(
                                    id="file-in-raw-history",
                                    name="history.txt",
                                    mime="text/plain",
                                )
                            ),
                        ]
                    )
                ]
            ),
            ModelResponse(parts=[TextPart(content="旧回复")]),
        ]
        await self.stores.coordinator.append(
            session_id,
            raw,
            user_id="user-1",
        )
        projected = [
            ModelRequest(
                parts=[UserPromptPart(content="旧历史摘要，不包含附件引用")]
            )
        ]
        self.assertTrue(
            await save_projection(
                self.stores.context_checkpoint,
                raw_history=raw,
                projected_history=projected,
                session_id=session_id,
                agent_id=None,
                fingerprint=self._fingerprint(),
                expected_row_version=0,
                summary_model_id="summary-model",
            )
        )
        capability = DeepAgentContextCompaction(
            context_window_tokens=4_000,
            max_output_tokens=500,
            summary_timeout=10,
        )
        runner = self._runner(capability)
        deps = AgentDeps(session_id=session_id, user="user-1")

        prepared = await runner._prepare_history(
            session_id,
            "继续读取原附件",
            deps,
        )

        self.assertTrue(prepared.checkpoint and prepared.checkpoint.used_checkpoint)
        self.assertIn("file-in-raw-history", deps.attachment_by_id)
        self.assertNotIn("file-in-raw-history", str(prepared.projected))

    async def test_stream_emits_transient_compaction_status_before_model_output(
        self,
    ) -> None:
        raw_before = _long_tool_history()
        await self.stores.coordinator.append(
            "compact-stream",
            raw_before,
            user_id="user-1",
        )
        capability = DeepAgentContextCompaction(
            context_window_tokens=4_000,
            max_output_tokens=500,
            summary_timeout=10,
        )
        events: list[dict] = []
        async for event in self._runner(capability).run_stream(
            "compact-stream",
            "继续",
            AgentDeps(session_id="compact-stream", user="user-1"),
            persister=self.persister,
        ):
            events.append(event)

        types = [event["type"] for event in events]
        self.assertIn("context_compaction", types)
        self.assertLess(
            types.index("context_compaction"),
            min(
                index
                for index, event_type in enumerate(types)
                if event_type in {"text", "thinking", "tool_call", "final"}
            ),
        )
        self.assertEqual(events[-1]["type"], "final")
        self.assertTrue(events[-1]["context"]["compaction"]["checkpoint_saved"])
        persisted = await self.stores.coordinator.get_history("compact-stream")
        self.assertNotIn("context_compaction", str(persisted))

    async def test_manual_compaction_writes_checkpoint_without_rewriting_history(
        self,
    ) -> None:
        ref = ContextReference(
            key="selection",
            type="deeptable.selection",
            schema_version="2.0",
            payload={"selection": "B2:C4"},
            metadata={"history_only_secret": "manual-metadata"},
        )
        raw: list[ModelMessage] = []
        for turn in range(3):
            repeats = 100 if turn == 1 else 20
            prompt: str | list[object] = (
                [
                    context_reference_dict(ref),
                    f"第 {turn} 轮约束：" + ("需要保留的中文背景。" * repeats),
                ]
                if turn == 0
                else f"第 {turn} 轮约束：" + ("需要保留的中文背景。" * repeats)
            )
            raw.extend(
                [
                    ModelRequest(
                        parts=[
                            UserPromptPart(content=prompt)
                        ]
                    ),
                    ModelResponse(
                        parts=[TextPart(content=f"第 {turn} 轮结论：" + ("确认当前决定。" * repeats))]
                    ),
                ]
            )
        await self.stores.coordinator.append(
            "manual-compact",
            raw,
            user_id="user-1",
        )
        capability = DeepAgentContextCompaction(
            context_window_tokens=4_000,
            max_output_tokens=500,
            summary_timeout=10,
        )
        runner = self._runner(capability)

        events = [
            event
            async for event in runner.run_manual_compaction(
                "manual-compact",
                AgentDeps(session_id="manual-compact", user="user-1"),
            )
        ]

        self.assertIn("started", [e.get("phase") for e in events])
        self.assertIn("deterministic", [e.get("phase") for e in events])
        self.assertIn("summarizing", [e.get("phase") for e in events])
        self.assertIn("saving", [e.get("phase") for e in events])
        result = events[-1]
        self.assertEqual(result["type"], "context_compaction_result")
        self.assertEqual(result["status"], "completed", result)
        self.assertGreater(result["usage"]["requests"], 0)
        self.assertLess(result["after_tokens"], result["before_tokens"])

        self.assertEqual(
            history_hash(await self.stores.coordinator.get_history("manual-compact")),
            history_hash(raw),
        )
        checkpoint = await self.stores.context_checkpoint.get("manual-compact")
        self.assertIsNotNone(checkpoint)
        assert checkpoint is not None
        self.assertEqual(checkpoint.row_version, 1)
        self.assertNotIn("manual-metadata", str(checkpoint.payload))

        repeated = [
            event
            async for event in runner.run_manual_compaction(
                "manual-compact",
                AgentDeps(session_id="manual-compact", user="user-1"),
            )
        ]
        self.assertEqual(repeated[-1]["status"], "noop")
        self.assertEqual(repeated[-1]["reason"], "no_reduction")
        unchanged = await self.stores.context_checkpoint.get("manual-compact")
        self.assertIsNotNone(unchanged)
        assert unchanged is not None
        self.assertEqual(unchanged.row_version, 1)

        tail = [
            ModelRequest(
                parts=[UserPromptPart(content="新增约束：" + "保留新增中文背景。" * 100)]
            ),
            ModelResponse(
                parts=[TextPart(content="新增结论：" + "确认新增决定。" * 100)]
            ),
        ]
        await self.stores.coordinator.append(
            "manual-compact",
            tail,
            user_id="user-1",
        )
        refreshed = [
            event
            async for event in runner.run_manual_compaction(
                "manual-compact",
                AgentDeps(session_id="manual-compact", user="user-1"),
            )
        ]
        self.assertEqual(refreshed[-1]["status"], "completed", refreshed[-1])
        updated = await self.stores.context_checkpoint.get("manual-compact")
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.row_version, 2)
        self.assertEqual(updated.source_last_sequence, len(raw) + len(tail) - 1)

    async def test_manual_compaction_does_not_resummarize_checkpoint_without_new_messages(
        self,
    ) -> None:
        raw = [
            ModelRequest(parts=[UserPromptPart(content="原始任务" + "背景" * 200)]),
            ModelResponse(parts=[TextPart(content="原始结论" + "决定" * 200)]),
            ModelRequest(parts=[UserPromptPart(content="继续处理" + "约束" * 200)]),
            ModelResponse(parts=[TextPart(content="继续结论" + "状态" * 200)]),
        ]
        await self.stores.coordinator.append(
            "manual-recompact",
            raw,
            user_id="user-1",
        )
        capability = DeepAgentContextCompaction(
            context_window_tokens=4_000,
            max_output_tokens=500,
            summary_timeout=10,
        )
        runner = self._runner(capability)
        projections = [
            [
                ModelRequest(parts=[UserPromptPart(content="摘要：保留原始任务和约束")]),
                ModelResponse(parts=[TextPart(content="最近结论")]),
            ],
            [
                ModelRequest(parts=[UserPromptPart(content="摘要：任务与约束")]),
                ModelResponse(parts=[TextPart(content="结论")]),
            ],
        ]
        with patch.object(
            capability,
            "compact_manually",
            new=AsyncMock(side_effect=projections),
        ):
            first = [
                event
                async for event in runner.run_manual_compaction(
                    "manual-recompact",
                    AgentDeps(session_id="manual-recompact", user="user-1"),
                )
            ]
            second = [
                event
                async for event in runner.run_manual_compaction(
                    "manual-recompact",
                    AgentDeps(session_id="manual-recompact", user="user-1"),
                )
            ]

        self.assertEqual(first[-1]["status"], "completed")
        self.assertEqual(second[-1]["status"], "noop")
        self.assertEqual(second[-1]["reason"], "no_reduction")
        checkpoint = await self.stores.context_checkpoint.get("manual-recompact")
        self.assertIsNotNone(checkpoint)
        assert checkpoint is not None
        self.assertEqual(checkpoint.row_version, 1)
        self.assertEqual(checkpoint.source_last_sequence, len(raw) - 1)
        self.assertEqual(
            history_hash(await self.stores.coordinator.get_history("manual-recompact")),
            history_hash(raw),
        )

    async def test_manual_compaction_empty_and_short_history_are_noop(self) -> None:
        capability = DeepAgentContextCompaction(
            context_window_tokens=4_000,
            max_output_tokens=500,
            summary_timeout=10,
        )
        runner = self._runner(capability)
        empty = [
            event
            async for event in runner.run_manual_compaction(
                "manual-empty",
                AgentDeps(session_id="manual-empty", user="user-1"),
            )
        ]
        self.assertEqual(empty[-1]["status"], "noop")
        self.assertEqual(empty[-1]["reason"], "empty_history")

        await self.stores.coordinator.append(
            "manual-short",
            [
                ModelRequest(parts=[UserPromptPart(content="你好")]),
                ModelResponse(parts=[TextPart(content="你好")]),
            ],
            user_id="user-1",
        )
        short = [
            event
            async for event in runner.run_manual_compaction(
                "manual-short",
                AgentDeps(session_id="manual-short", user="user-1"),
            )
        ]
        self.assertEqual(short[-1]["status"], "noop")
        self.assertEqual(short[-1]["reason"], "insufficient_history")
        self.assertEqual(short[-1]["usage"]["requests"], 0)

    async def test_manual_compaction_failure_and_cas_conflict_preserve_history(
        self,
    ) -> None:
        def long_history() -> list[ModelMessage]:
            messages: list[ModelMessage] = []
            for turn in range(3):
                repeats = 100 if turn == 1 else 20
                messages.extend(
                    [
                        ModelRequest(
                            parts=[
                                UserPromptPart(
                                    content=f"第 {turn} 轮背景：" + "保留约束。" * repeats
                                )
                            ]
                        ),
                        ModelResponse(
                            parts=[TextPart(content=f"第 {turn} 轮结论：" + "确认决定。" * repeats)]
                        ),
                    ]
                )
            return messages

        def fail_summary(
            messages: list[ModelMessage],
            _info: AgentInfo,
        ) -> ModelResponse:
            if "<history>" in str(messages):
                raise RuntimeError("provider unavailable")
            return ModelResponse(parts=[TextPart(content="unused")])

        failed_raw = long_history()
        await self.stores.coordinator.append(
            "manual-provider-failure",
            failed_raw,
            user_id="user-1",
        )
        capability = DeepAgentContextCompaction(
            context_window_tokens=4_000,
            max_output_tokens=500,
            summary_timeout=10,
        )
        failed = [
            event
            async for event in self._runner(
                capability,
                model=FunctionModel(fail_summary),
            ).run_manual_compaction(
                "manual-provider-failure",
                AgentDeps(session_id="manual-provider-failure", user="user-1"),
            )
        ]
        self.assertEqual(failed[-1]["status"], "failed")
        self.assertEqual(failed[-1]["reason"], "summary_provider_error")
        self.assertIsNone(
            await self.stores.context_checkpoint.get("manual-provider-failure")
        )
        self.assertEqual(
            history_hash(
                await self.stores.coordinator.get_history("manual-provider-failure")
            ),
            history_hash(failed_raw),
        )

        cas_raw = long_history()
        await self.stores.coordinator.append(
            "manual-cas-conflict",
            cas_raw,
            user_id="user-1",
        )
        with patch(
            "deep_agent.agent.runner.save_projection",
            new=AsyncMock(return_value=False),
        ):
            conflicted = [
                event
                async for event in self._runner(
                    DeepAgentContextCompaction(
                        context_window_tokens=4_000,
                        max_output_tokens=500,
                        summary_timeout=10,
                    )
                ).run_manual_compaction(
                    "manual-cas-conflict",
                    AgentDeps(session_id="manual-cas-conflict", user="user-1"),
                )
            ]
        self.assertEqual(conflicted[-1]["status"], "failed")
        self.assertEqual(conflicted[-1]["reason"], "session_changed")
        self.assertIsNone(
            await self.stores.context_checkpoint.get("manual-cas-conflict")
        )
        self.assertEqual(
            history_hash(await self.stores.coordinator.get_history("manual-cas-conflict")),
            history_hash(cas_raw),
        )

    async def test_model_response_usage_survives_database_serialization(
        self,
    ) -> None:
        original = ModelResponse(
            parts=[TextPart(content="完成")],
            usage=RequestUsage(
                input_tokens=10_451,
                output_tokens=187,
                cache_read_tokens=9_984,
                details={"reasoning_tokens": 111},
            ),
        )
        restored = deserialize_message(serialize_message(original))

        self.assertIsInstance(restored, ModelResponse)
        self.assertEqual(restored.usage.input_tokens, 10_451)
        self.assertEqual(restored.usage.output_tokens, 187)
        self.assertEqual(restored.usage.cache_read_tokens, 9_984)
        self.assertEqual(restored.usage.details["reasoning_tokens"], 111)

    async def test_single_current_user_message_over_window_is_explicit_error(
        self,
    ) -> None:
        capability = DeepAgentContextCompaction(
            context_window_tokens=4_000,
            max_output_tokens=500,
            summary_timeout=10,
        )

        with self.assertRaises(ContextWindowExceededError) as raised:
            await self._runner(capability).run(
                "oversized-current-input",
                "中" * 6_000,
                AgentDeps(
                    session_id="oversized-current-input",
                    user="user-1",
                ),
            )

        self.assertIn("请缩短内容", str(raised.exception))
        raw = await self.stores.coordinator.get_history(
            "oversized-current-input"
        )
        self.assertEqual(len(raw), 1)

    async def test_single_context_reference_over_window_is_explicit_error(self) -> None:
        capability = DeepAgentContextCompaction(
            context_window_tokens=4_000,
            max_output_tokens=500,
            summary_timeout=10,
        )
        ref = ContextReference(
            key="large_selection",
            type="deeptable.selection",
            schema_version="2.0",
            label="超大选区",
            payload={"cells": "中" * 6_000},
            metadata={"debug": "not-visible"},
        )

        with self.assertRaises(ContextWindowExceededError):
            await self._runner(capability).run(
                "oversized-context-ref",
                "",
                AgentDeps(session_id="oversized-context-ref", user="user-1"),
                context_refs=[ref],
            )

        raw = await self.stores.coordinator.get_history("oversized-context-ref")
        self.assertEqual(len(raw), 1)
        self.assertIn("not-visible", str(serialize_message(raw[0])))


if __name__ == "__main__":
    unittest.main()
