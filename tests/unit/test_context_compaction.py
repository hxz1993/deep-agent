from __future__ import annotations

import asyncio
import json
import time
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage

from deep_agent.agent.context.compaction import (
    DeepAgentContextCompaction,
    SummaryUnavailable,
    _deterministic_attachment_evidence_text,
    _deterministic_context_evidence_text,
    _formatted_history,
    _protected_messages,
    _raw_prefix_for_visible_cutoff,
    _safe_boundaries,
    _prune_tool_results,
    _jsonable_usage,
    _summary_anchor,
    _summary_materials,
    _run_preparation_step,
    is_provider_context_overflow,
    summary_contents,
    validate_summary_markdown,
)
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.context.runtime import ContextRunState


def valid_summary() -> str:
    titles = (
        "Primary Request and Intent", "Key Technical Concepts", "Files and Code",
        "Errors and Fixes", "Pending Jobs", "Current Work", "Next Step",
        "Critical Context",
    )
    return "\n\n".join(
        f"## {index}. {title}\n{'内容' if index == 1 else '无'}"
        for index, title in enumerate(titles, start=1)
    )


class DshCompactionRulesTests(unittest.TestCase):
    def test_summary_usage_is_recursively_json_safe(self) -> None:
        usage = RunUsage(
            input_tokens=12,
            output_tokens=3,
            cost=Decimal("0.0008047872"),
        )

        serialized = _jsonable_usage(usage)

        self.assertEqual(serialized["cost"], "0.0008047872")
        self.assertEqual(serialized["input_tokens"], 12)
        self.assertEqual(serialized["output_tokens"], 3)
        json.dumps(serialized)

    def test_provider_overflow_detection_is_explicit_and_narrow(self) -> None:
        self.assertTrue(
            is_provider_context_overflow(
                ModelHTTPError(
                    400,
                    "test-model",
                    {"error": {"code": "context_length_exceeded"}},
                )
            )
        )
        self.assertTrue(
            is_provider_context_overflow(
                ModelHTTPError(413, "test-model", "maximum context length exceeded")
            )
        )
        self.assertFalse(
            is_provider_context_overflow(
                ModelHTTPError(400, "test-model", {"error": "invalid api key"})
            )
        )
        self.assertFalse(
            is_provider_context_overflow(
                ModelHTTPError(503, "test-model", "maximum context length exceeded")
            )
        )

    def test_eight_section_summary_is_strict(self) -> None:
        validate_summary_markdown(valid_summary())
        with self.assertRaises(SummaryUnavailable):
            validate_summary_markdown(valid_summary().replace("## 8. Critical Context", ""))
        with self.assertRaises(SummaryUnavailable):
            validate_summary_markdown(valid_summary().replace("## 2.", "## 9."))

    def test_tool_result_8192_boundary_and_8193_pruning(self) -> None:
        unchanged = ModelRequest(parts=[ToolReturnPart("query", "甲" * 8192, "call-1")])
        pruned, records = _prune_tool_results(
            [unchanged], threshold=8192, head=4096, tail=1024
        )
        self.assertEqual(pruned, [unchanged])
        self.assertEqual(records, [])

        oversized = ModelRequest(parts=[ToolReturnPart("query", "😀" * 8193, "call-2")])
        pruned, records = _prune_tool_results(
            [oversized], threshold=8192, head=4096, tail=1024
        )
        content = pruned[0].parts[0].content
        self.assertTrue(content.startswith("😀" * 4096))
        self.assertTrue(content.endswith("😀" * 1024))
        self.assertIn("tool result middle pruned", content)
        self.assertEqual(records[0]["before_codepoints"], 8193)
        self.assertEqual(oversized.parts[0].content, "😀" * 8193)

    def test_non_text_blocks_keep_order(self) -> None:
        marker = {"kind": "custom", "id": "binary"}
        message = ModelRequest(
            parts=[ToolReturnPart("query", [marker, "中" * 8193, marker], "call-3")]
        )
        pruned, _ = _prune_tool_results(
            [message], threshold=8192, head=4096, tail=1024
        )
        blocks = pruned[0].parts[0].content
        self.assertEqual(blocks[0], marker)
        self.assertEqual(blocks[2], marker)

    def test_tool_pair_is_never_split_by_safe_boundary(self) -> None:
        messages = [
            ModelRequest(parts=[UserPromptPart(content="查成员")]),
            ModelResponse(parts=[ToolCallPart("query", {}, "call-4")]),
            ModelRequest(parts=[ToolReturnPart("query", "结果", "call-4")]),
            ModelResponse(parts=[TextPart(content="完成")]),
        ]
        boundaries = _safe_boundaries(messages)
        self.assertNotIn(2, boundaries)
        self.assertIn(3, boundaries)

    def test_current_turn_is_preserved_as_one_unit(self) -> None:
        messages = [
            ModelRequest(parts=[UserPromptPart(content="第一轮")]),
            ModelResponse(parts=[TextPart(content="旧回答")]),
            ModelRequest(parts=[UserPromptPart(content="当前轮")]),
            ModelResponse(parts=[ToolCallPart("query", {}, "call-current")]),
            ModelRequest(parts=[ToolReturnPart("query", "结果", "call-current")]),
            ModelResponse(parts=[TextPart(content="当前回答")]),
        ]
        self.assertEqual(_protected_messages(messages), messages[2:])

    def test_visible_compaction_prefix_maps_back_to_raw_events(self) -> None:
        raw = [
            ModelRequest(parts=[UserPromptPart(content=f"用户-{index}")])
            if index % 2 == 0
            else ModelResponse(parts=[TextPart(content=f"回答-{index}")])
            for index in range(6)
        ]
        anchor = ModelRequest(
            parts=[
                UserPromptPart(
                    content=(
                        "Summary of previous conversation:\n\n"
                        f"{valid_summary()}\n\n"
                        "[DeepAgent deterministic evidence]\n历史证据"
                    )
                )
            ]
        )
        visible = [anchor, *raw[-4:]]
        self.assertEqual(summary_contents(visible), (valid_summary(),))
        self.assertEqual(
            _raw_prefix_for_visible_cutoff(visible, raw, 3),
            raw[:4],
        )

    def test_summary_cutoff_scans_history_once(self) -> None:
        messages = [
            item
            for index in range(80)
            for item in (
                ModelRequest(parts=[UserPromptPart(content=f"问题-{index}-" + "甲" * 80)]),
                ModelResponse(parts=[TextPart(content=f"回答-{index}-" + "乙" * 80)]),
            )
        ]
        delegate = TokenEstimator.conservative()

        class CountingEstimator:
            source = delegate.source

            def __init__(self) -> None:
                self.request_calls = 0
                self.message_calls = 0

            def request(self, values, parameters=None):
                self.request_calls += 1
                return delegate.request(values, parameters)

            def message(self, value):
                self.message_calls += 1
                return delegate.message(value)

            def text(self, value):
                return delegate.text(value)

        estimator = CountingEstimator()
        compaction = DeepAgentContextCompaction(
            context_window_tokens=200_000,
            max_output_tokens=4_096,
        )
        cutoff = compaction._single_summary_cutoff(
            messages,
            SimpleNamespace(model_request_parameters=None),
            target=9_000,
            output_limit=1_000,
            previous_summary=None,
            estimator=estimator,
        )

        self.assertGreater(cutoff, 0)
        self.assertEqual(estimator.request_calls, 1)
        self.assertEqual(estimator.message_calls, len(messages) + 1)

    def test_context_payload_is_evidence_not_summary_input(self) -> None:
        raw = ModelRequest(
            parts=[
                UserPromptPart(
                    content=[
                        {
                            "kind": "context-ref",
                            "key": "selection",
                            "type": "region",
                            "schema_version": 1,
                            "label": "当前区域",
                            "payload": {"id": "member-42", "name": "华东"},
                            "metadata": {"secret": "must-not-enter"},
                        },
                        "请分析",
                    ]
                )
            ]
        )
        formatted = _formatted_history([raw])
        self.assertIn("请分析", formatted)
        self.assertNotIn("member-42", formatted)
        self.assertNotIn("must-not-enter", formatted)
        evidence = _deterministic_context_evidence_text([raw])
        self.assertIn("member-42", evidence)
        self.assertIn("华东", evidence)
        self.assertNotIn("must-not-enter", evidence)

    def test_attachment_reference_is_deterministic_evidence(self) -> None:
        raw = ModelRequest(
            parts=[
                UserPromptPart(
                    content=[
                        "请读取附件",
                        {
                            "kind": "attachment-ref",
                            "id": "local_123",
                            "name": "预算.xlsx",
                            "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        },
                    ]
                )
            ]
        )
        evidence = _deterministic_attachment_evidence_text([raw])
        self.assertIn("Verified Attachment Evidence", evidence)
        self.assertIn("local_123", evidence)
        self.assertIn("预算.xlsx", evidence)
        self.assertIn("reference_hash", evidence)

    def test_rendered_context_projection_is_removed_from_summary_input(self) -> None:
        projected = ModelRequest(
            parts=[
                UserPromptPart(
                    content=[
                        '<context_references>{"id":"member-99"}</context_references>\n'
                        "<user_message>继续分析</user_message>"
                    ]
                )
            ]
        )
        formatted = _formatted_history([projected])
        self.assertNotIn("member-99", formatted)
        self.assertIn("继续分析", formatted)


class CompactionPreparationProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_incremental_summary_reserves_evidence_before_model_call(self) -> None:
        raw = [ModelRequest(parts=[UserPromptPart(content="较早任务")])]
        for i in range(45):
            raw.extend([
                ModelResponse(parts=[ToolCallPart("query", {"id": str(i)}, str(i))]),
                ModelRequest(parts=[ToolReturnPart("query", {"id": str(i), "name": "甲" * 180}, str(i))]),
            ])
        evidence = _summary_materials(raw, raw, len(raw))[2]
        tail = [
            ModelRequest(parts=[UserPromptPart(content="乙" * 70_000)]),
            ModelResponse(parts=[TextPart(content="完成第一步")]),
            ModelRequest(parts=[UserPromptPart(content="丙" * 41_000)]),
            ModelResponse(parts=[TextPart(content="完成第二步")]),
            ModelRequest(parts=[UserPromptPart(content="丁" * 48_000)]),
            ModelResponse(parts=[TextPart(content="完成第三步")]),
        ]
        messages = [_summary_anchor(valid_summary(), evidence), *tail]
        raw = [*raw, *tail]
        est = TokenEstimator.conservative()
        state = ContextRunState(raw_history=raw, estimator=est)
        state.telemetry.estimated_tokens_before = est.request(messages)[0]
        comp = DeepAgentContextCompaction(context_window_tokens=1_000_000, max_output_tokens=384_000)
        summary = AsyncMock(return_value=valid_summary() + "\n" + "戊" * 2_000)
        with patch.object(DeepAgentContextCompaction, "_single_summary", summary):
            result, after = await comp._compact_projection(
                messages, SimpleNamespace(), SimpleNamespace(model_request_parameters=None),
                96_474, state, require_summary=True, emit_deterministic=False,
                reserve_answer_request=False,
            )
        self.assertEqual(summary.await_count, 1)
        self.assertLessEqual(after, 96_474)
        self.assertGreater(state.telemetry.evidence_tokens, 4_000)
        self.assertEqual(result[-2:], tail[-2:])
        self.assertEqual(state.raw_history, raw)

    async def test_target_failure_records_candidate_without_applying_it(self) -> None:
        est = TokenEstimator.conservative()
        original = [ModelRequest(parts=[UserPromptPart(content="原文" * 2000)])]
        candidate = [_summary_anchor(valid_summary() + "甲" * 1500, ())]
        state = ContextRunState(raw_history=original, estimator=est)
        before = est.request(original)[0]
        state.telemetry.estimated_tokens_before = before
        state.telemetry.estimated_tokens_after = before
        comp = DeepAgentContextCompaction(context_window_tokens=100_000, max_output_tokens=4096)
        with patch.object(DeepAgentContextCompaction, "_summarize_projection", AsyncMock(return_value=candidate)):
            with self.assertRaisesRegex(SummaryUnavailable, "target_not_met"):
                await comp._compact_projection(
                    original, SimpleNamespace(), SimpleNamespace(model_request_parameters=None),
                    1000, state, require_summary=True, emit_deterministic=False,
                    reserve_answer_request=False,
                )
        self.assertEqual(state.telemetry.candidate_tokens, est.request(candidate)[0])
        self.assertEqual(state.telemetry.estimated_tokens_after, before)
        self.assertFalse(state.telemetry.summary_applied)

    async def test_cpu_preparation_yields_periodic_sse_progress(self) -> None:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        state = ContextRunState(
            raw_history=[],
            estimator=TokenEstimator.conservative(),
            event_queue=queue,
        )

        with patch(
            "deep_agent.agent.context.compaction._SUMMARY_HEARTBEAT_SECONDS",
            0.01,
        ):
            result = await _run_preparation_step(
                lambda: (time.sleep(0.04), "done")[1],
                state,
                message="正在准备测试历史…",
            )

        self.assertEqual(result, "done")
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        self.assertGreaterEqual(len(events), 3)
        self.assertTrue(all(event["phase"] == "preparing" for event in events))


if __name__ == "__main__":
    unittest.main()
