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
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
    BinaryContent,
    SystemPromptPart,
)
from pydantic_ai import RunContext
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage, UsageLimits

from deep_agent.agent.context.compaction import (
    DeepAgentContextCompaction,
    SummaryUnavailable,
    _deterministic_attachment_evidence_text,
    _deterministic_context_evidence_text,
    _formatted_history,
    _protected_messages,
    _prepare_summary_prompt,
    _raw_prefix_for_visible_cutoff,
    _safe_boundaries,
    _jsonable_usage,
    _summary_anchor,
    _summary_output_limit,
    _summary_materials,
    _run_preparation_step,
    is_provider_context_overflow,
    summary_contents,
    validate_summary_markdown,
)
from deep_agent.agent.context.handoff import (
    extract_protected_context, extract_anchor_protected_context, render_handoff_anchor, safe_tool_args_preview,
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
    def test_handoff_parser_rejects_malformed_metadata_and_redacts_nested_binary(self) -> None:
        from deep_agent.agent.context.handoff import PROTECTED_CONTEXT_MARKER, SUMMARY_PREFIX, latest_user_has_unreferenced_media
        messages = [ModelResponse(parts=[TextPart("ignore")]), ModelRequest(parts=[UserPromptPart("real"),
            UserPromptPart(SUMMARY_PREFIX + "old" + PROTECTED_CONTEXT_MARKER + "not json"),
            UserPromptPart(SUMMARY_PREFIX + "old" + PROTECTED_CONTEXT_MARKER + "[]")])]
        self.assertEqual(extract_anchor_protected_context(messages), {})
        self.assertFalse(latest_user_has_unreferenced_media([ModelResponse(parts=[TextPart("done")])]))
        projected = extract_protected_context([ModelRequest(parts=[UserPromptPart([{"kind": "text-content", "content": "文字"}])])])
        self.assertEqual(projected["latest_user_content"], [["文字"]])
        preview = json.loads(safe_tool_args_preview({"items": [b"secret", 1, {"token": "secret"}]}))
        self.assertEqual(preview["items"], ["[binary omitted]", 1, {"token": "[redacted]"}])

    def test_summary_preparation_rejects_unshrinkable_text_and_bounds_second_preview_pass(self) -> None:
        est = TokenEstimator.conservative()
        with self.assertRaisesRegex(SummaryUnavailable, "summary_input_too_large"):
            _prepare_summary_prompt([ModelRequest(parts=[UserPromptPart("不可省略的要求" * 2000)])], None, 256, 2500, est)
        # Several results require the smaller second-pass preview; a short result
        # and user-controlled preview-like keys must still retain their meaning.
        messages = [ModelRequest(parts=[ToolReturnPart("query", "结果" * 10000, str(index)) for index in range(3)]),
                    ModelRequest(parts=[ToolReturnPart("query", {"compaction_preview": True, "actual": 1}, "short")])]
        prompt, previews = _prepare_summary_prompt(messages, "此前完成预算核对", 256, 6000, est)
        self.assertTrue(all(item["after_codepoints"] == 256 for item in previews))
        self.assertIn('"actual":1', prompt)
        self.assertIn("此前完成预算核对", prompt)
        self.assertEqual(len(previews), 3)

    def test_tail_utilities_never_separate_a_tool_call_and_result(self) -> None:
        from deep_agent.agent.context.compaction import _safe_tail_cutoff, _logical_units, _strip_summary_and_system
        messages = [ModelRequest(parts=[UserPromptPart("a")]),
                    ModelResponse(parts=[ToolCallPart("query", {}, "one")]),
                    ModelRequest(parts=[ToolReturnPart("query", "large result" * 100, "one")]),
                    ModelResponse(parts=[TextPart("done")])]
        est = TokenEstimator.conservative()
        cutoff = _safe_tail_cutoff(messages, keep_tokens=est.message(messages[-1]), estimator=est)
        self.assertEqual(cutoff, 3)
        self.assertEqual(_safe_tail_cutoff(messages, keep_tokens=100000, estimator=est), 0)
        self.assertEqual(_logical_units(messages), [messages[:1], messages[1:3], messages[3:]])
        system = ModelRequest(parts=[SystemPromptPart("required"), UserPromptPart("a")])
        self.assertEqual(_strip_summary_and_system([system])[0].parts, [system.parts[1]])
        self.assertEqual(_raw_prefix_for_visible_cutoff(messages, messages, 0), [])
        self.assertEqual(_raw_prefix_for_visible_cutoff(messages[:2], messages, 1), messages[:3])

    def test_error_wrapping_and_provider_retry_headers_are_bounded(self) -> None:
        from deep_agent.agent.context.compaction import _status_code, _retry_after_seconds, _is_transient_summary_error, _jsonable_usage
        error = RuntimeError("transport")
        error.response = SimpleNamespace(status_code=503, headers={"Retry-After": "999999"})
        wrapped = RuntimeError("outer")
        wrapped.__cause__ = error
        self.assertEqual(_status_code(wrapped), 503)
        self.assertTrue(_is_transient_summary_error(wrapped))
        self.assertLessEqual(_retry_after_seconds(wrapped), 10)
        error.response.headers = {"Retry-After": "invalid"}
        error.__cause__ = wrapped  # provider wrappers can contain cycles
        self.assertEqual(_retry_after_seconds(wrapped), 0)
        self.assertIsNone(_status_code(ValueError("no status")))
        self.assertFalse(_is_transient_summary_error(ValueError("not transient")))
        self.assertIsNone(_jsonable_usage(lambda: None))

    def test_planner_keeps_system_prefix_when_no_safe_content_can_be_replaced(self) -> None:
        from deep_agent.agent.context.compaction import _stable_facts, build_context_compaction
        est = TokenEstimator.conservative()
        comp = DeepAgentContextCompaction(context_window_tokens=100000, max_output_tokens=256)
        for messages in ([], [ModelRequest(parts=[SystemPromptPart("instruction")]), ModelRequest(parts=[UserPromptPart("user")])],
                         [ModelResponse(parts=[ToolCallPart("q", {}, "pending")])]):
            state = ContextRunState(raw_history=messages, estimator=est)
            with self.assertRaisesRegex(SummaryUnavailable, "no_safe_summary_prefix"):
                comp._plan_summary(messages, SimpleNamespace(model_request_parameters=None), budget_limit=80000,
                                   output_limit=256, previous_summary=None, state=state)
        self.assertEqual(_stable_facts({"nested": [{"name": ["甲", "乙"], "total": 2}]}), {"name": ["甲", "乙"], "total": 2})
        self.assertEqual(_deterministic_attachment_evidence_text([ModelRequest(parts=[UserPromptPart([{"kind": "attachment-ref"}])])]), "")
        self.assertIsNone(build_context_compaction(context_window_tokens=None, max_output_tokens=None))

    def test_authentic_raw_user_can_start_with_the_synthetic_summary_prefix(self) -> None:
        from deep_agent.agent.context.handoff import SUMMARY_PREFIX
        content = SUMMARY_PREFIX + "这是用户提供的原始材料，单位万元；禁止写入。"
        raw = [ModelRequest(parts=[UserPromptPart(content)]), ModelResponse(parts=[TextPart("确认")])]
        self.assertEqual(extract_protected_context(raw), {})
        protected = extract_protected_context(raw, cutoff=2, raw_history=raw)
        self.assertEqual(protected["latest_user_content"], [content])
        self.assertEqual(protected["source_message_index"], 0)
        self.assertEqual(extract_protected_context(raw, cutoff=0, raw_history=raw), {})

    def test_history_reads_do_not_recursively_grow_business_evidence(self) -> None:
        from deep_agent.agent.context.compaction import _deterministic_evidence_text
        from deep_agent.db.conversation import _tool_evidence, _bounded_evidence
        from deep_agent.db.event_codec import encode_messages
        original = {"id": "report-1", "revenue": 360, "name": ["scope-must-not-be-flattened"]}
        messages = [ModelResponse(parts=[ToolCallPart("query_finance", {"period": "2026", "token": "secret"}, "business-1")]),
                    ModelRequest(parts=[ToolReturnPart("query_finance", original, "business-1")])]
        def db_evidence():
            drafts = encode_messages(messages, session_id="evidence-fixture", run_id="read-fixture")
            rows = [SimpleNamespace(event_id=item.event_id, event_type=item.event_type,
                    payload=item.payload, sequence=index + 1) for index, item in enumerate(drafts)]
            return _bounded_evidence(_tool_evidence(rows))
        baseline = db_evidence()
        runtime_before = _deterministic_evidence_text(messages)
        for index in range(8):
            name = "read_conversation_event" if index % 2 else "search_conversation_history"
            messages.extend([ModelResponse(parts=[ToolCallPart(name, {"call_id": "business-1", "offset": index * 1000}, str(index))]),
                             ModelRequest(parts=[ToolReturnPart(name, {"events": [original], "next_read": {"offset": (index + 1) * 1000}}, str(index))])])
        self.assertEqual(db_evidence(), baseline)
        self.assertEqual(_deterministic_evidence_text(messages), runtime_before)
        self.assertNotIn("args_hash", runtime_before)
        self.assertNotIn("result_hash", runtime_before)
        self.assertNotIn("stable_facts", runtime_before)
        self.assertNotIn("secret", baseline[0]["args_preview"])
        self.assertIn("2026", baseline[0]["args_preview"])
        self.assertEqual(baseline[0]["outcome"], "success")
        self.assertTrue(baseline[0]["result_event_id"])
        self.assertEqual(baseline[0]["result_part_offset"], 0)
        materials = _summary_materials(messages, messages, len(messages))[0]
        self.assertEqual(len(materials), len(messages))
        self.assertIn("next_read", _formatted_history(materials))
        self.assertEqual(messages[1].parts[0].content, original)

    def test_failed_history_reads_keep_short_diagnostics_and_budget_fallback_keeps_arguments(self) -> None:
        from deep_agent.agent.context.compaction import _deterministic_evidence_text
        from deep_agent.agent.context.handoff import project_tool_evidence
        from deep_agent.db.conversation import _tool_evidence, _bounded_evidence
        from deep_agent.db.event_codec import encode_messages
        messages = [ModelResponse(parts=[ToolCallPart("read_conversation_event", {"call_id": "missing"}, "retry")]),
                    ModelRequest(parts=[RetryPromptPart("Historical event not found; do not repeat this locator.", tool_name="read_conversation_event", tool_call_id="retry")])]
        drafts = encode_messages(messages, session_id="failed-read", run_id="run")
        rows = [SimpleNamespace(event_id=item.event_id, event_type=item.event_type,
                payload=item.payload, sequence=index + 1) for index, item in enumerate(drafts)]
        evidence = _bounded_evidence(_tool_evidence(rows))
        self.assertEqual(evidence[0]["outcome"], "failed")
        self.assertIn("not found", evidence[0]["diagnostic"])
        self.assertIn("not found", _deterministic_evidence_text(messages))
        long_entry = {**evidence[0], "args_preview": "period=2026; " + "x" * 1000}
        bounded = _bounded_evidence([long_entry], max_chars=650)
        self.assertEqual(len(bounded), 1)
        self.assertIn("period=2026", bounded[0]["args_preview"])
        self.assertTrue(bounded[0]["result_event_id"])
        self.assertEqual(project_tool_evidence({"tool_name": "custom", "call_id": "x"})["outcome"], "unknown")
        # Same-looking business tool names must not be filtered by substring.
        self.assertIsNotNone(project_tool_evidence({"tool_name": "read_conversation_event_custom", "outcome": "success"}))

    def test_summary_allowance_respects_model_and_global_caps(self) -> None:
        self.assertEqual(_summary_output_limit(9829, None), 4096)
        self.assertEqual(_summary_output_limit(100_000, None), 4096)
        self.assertEqual(_summary_output_limit(9829, 1024), 1024)
        self.assertEqual(_summary_output_limit(100, 1), 1)


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

    def test_handoff_summary_accepts_task_specific_structure(self) -> None:
        validate_summary_markdown("已查销售收入，下一步核对成本。")
        validate_summary_markdown("## 已确认\n收入360万元。\n\n## 继续\n核对利润口径。")
        with self.assertRaisesRegex(SummaryUnavailable, "summary_empty"):
            validate_summary_markdown("  ")

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

    def test_tool_retry_closes_only_its_call_before_later_turn(self) -> None:
        messages = [
            ModelRequest(parts=[UserPromptPart(content="读文件")]),
            ModelResponse(parts=[ToolCallPart("read_file", {}, "retry-a"), ToolCallPart("query", {}, "pending-b")]),
            ModelRequest(parts=[RetryPromptPart(content="文件不存在", tool_name="read_file", tool_call_id="retry-a")]),
            ModelRequest(parts=[RetryPromptPart(content="请修正输出格式")]),
            ModelRequest(parts=[ToolReturnPart("query", "完成", "pending-b")]),
            ModelRequest(parts=[UserPromptPart(content="下一轮")]),
            ModelResponse(parts=[TextPart(content="完成")]),
        ]
        boundaries = _safe_boundaries(messages)
        self.assertNotIn(2, boundaries)
        self.assertNotIn(3, boundaries)
        self.assertNotIn(4, boundaries)
        self.assertIn(5, boundaries)
        self.assertIn(7, boundaries)
        formatted = json.loads(_formatted_history(messages))
        retry = formatted[2]["parts"][0]
        self.assertEqual(retry["kind"], "tool_retry")
        self.assertEqual(retry["tool_call_id"], "retry-a")
        self.assertEqual(retry["content"], "文件不存在")
        self.assertEqual(_protected_messages(messages), messages[5:6])

    def test_only_latest_user_is_mandatory_after_tools_close(self) -> None:
        messages = [
            ModelRequest(parts=[UserPromptPart(content="第一轮")]),
            ModelResponse(parts=[TextPart(content="旧回答")]),
            ModelRequest(parts=[UserPromptPart(content="当前轮")]),
            ModelResponse(parts=[ToolCallPart("query", {}, "call-current")]),
            ModelRequest(parts=[ToolReturnPart("query", "结果", "call-current")]),
            ModelResponse(parts=[TextPart(content="当前回答")]),
        ]
        self.assertEqual(_protected_messages(messages), messages[2:3])

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
            budget_limit=9_000,
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

    def test_protected_context_is_exact_sourced_and_uses_safe_reference_projection(self) -> None:
        user = ModelRequest(parts=[UserPromptPart(content=[
            "  仅分析2026年，单位万元；不要写入。  ",
            {"kind": "context-ref", "key": "region", "type": "spreadsheet.selection", "schema_version": "1",
             "payload": {"period": "2026", "cube": "budget"}, "metadata": {"token": "must-not-copy"}},
            {"kind": "attachment-ref", "id": "file-1", "name": "actual.xlsx", "mime": "application/xlsx", "url": "secret-url"},
            BinaryContent(data=b"raw-image-bytes", media_type="image/png"),
        ])])
        raw = [user, ModelResponse(parts=[TextPart("已读取")])]
        protected = extract_protected_context(raw, cutoff=2)
        self.assertEqual(protected["latest_user_content"][0][0], "  仅分析2026年，单位万元；不要写入。  ")
        self.assertEqual(protected["source_message_index"], 0)
        self.assertEqual(len(protected["source_sha256"]), 64)
        self.assertTrue(protected["has_context_references"])
        rendered = render_handoff_anchor("下一步核对差异。", (), protected)
        text = rendered.parts[0].content
        self.assertIn("budget", text)
        self.assertIn("file-1", text)
        self.assertNotIn("must-not-copy", text)
        self.assertNotIn("secret-url", text)
        self.assertNotIn("raw-image-bytes", text)
        self.assertEqual(extract_anchor_protected_context([rendered]), protected)
        altered = [ModelRequest(parts=[UserPromptPart(content="更改期间")]), raw[1]]
        self.assertNotEqual(extract_protected_context(altered)["source_sha256"], protected["source_sha256"])

    def test_latest_user_in_suffix_is_not_duplicated_and_summary_is_not_a_user(self) -> None:
        raw = [ModelRequest(parts=[UserPromptPart(content="真正请求")]), ModelResponse(parts=[TextPart("已处理")])]
        protected = extract_protected_context(raw, cutoff=2)
        anchor = render_handoff_anchor("状态", (), protected)
        self.assertEqual(extract_protected_context([anchor]), {})
        self.assertEqual(extract_protected_context([anchor], raw_history=raw), protected)
        new_user = ModelRequest(parts=[UserPromptPart(content="最新纠正")])
        self.assertEqual(extract_protected_context([anchor, new_user], cutoff=1, raw_history=[*raw, new_user]), {})
        self.assertEqual(summary_contents([anchor]), ("状态",))

    def test_summary_receives_complete_result_when_request_fits(self) -> None:
        payload = "甲" * 10_000 + "MIDPOINT-EXACT-360.25" + "乙" * 10_000
        messages = [ModelResponse(parts=[ToolCallPart("query", {}, "read-1")]),
                    ModelRequest(parts=[ToolReturnPart("query", payload, "read-1")])]
        materials = _summary_materials(messages, messages, len(messages))
        self.assertEqual(materials[0][-1].parts[0].content, payload)
        self.assertEqual(materials[1], [])
        prompt, previews = _prepare_summary_prompt(messages, None, 512, 100_000, TokenEstimator.conservative())
        self.assertIn(payload, prompt)
        self.assertEqual(previews, [])

    def test_oversized_summary_result_has_bounded_preview_and_authorized_handle(self) -> None:
        payload = "甲" * 10_000 + "MIDPOINT-EXACT-360.25" + "乙" * 10_000
        result = ToolReturnPart("query", payload, "read-1", metadata={"overflow_handle": "tool-result:authorized"})
        messages = [ModelResponse(parts=[ToolCallPart("query", {}, "read-1")]), ModelRequest(parts=[result])]
        prompt, previews = _prepare_summary_prompt(messages, None, 512, 6_000, TokenEstimator.conservative())
        self.assertNotIn("MIDPOINT-EXACT-360.25", prompt)
        self.assertIn("read_tool_result", prompt)
        self.assertIn("tool-result:authorized", prompt)
        self.assertIn("do not infer missing values", prompt)
        self.assertEqual(previews[0]["call_id"], "read-1")
        self.assertEqual(result.content, payload)

    def test_evidence_args_preview_redacts_credentials_and_is_bounded(self) -> None:
        preview = safe_tool_args_preview({"cube": "actual", "headers": {"Authorization": "Bearer secret"},
                                          "url": "https://user:pass@example.test/result?token=secret", "rows": "x" * 2000}, 400)
        self.assertLessEqual(len(preview), 400)
        self.assertIn("actual", preview)
        self.assertNotIn("Bearer secret", preview)
        self.assertNotIn("user:pass", preview)
        self.assertNotIn("token=secret", preview)


class CompactionPreparationProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_system_and_unreferenced_media_keep_the_original_suffix(self) -> None:
        old = [ModelRequest(parts=[UserPromptPart("旧任务")]), ModelResponse(parts=[TextPart("旧结果" * 4000)])]
        for protected in (ModelRequest(parts=[SystemPromptPart("独立的系统约束不得降级")]),
                          ModelRequest(parts=[UserPromptPart(["分析图片", BinaryContent(data=b"image", media_type="image/png")])])):
            messages = [*old, protected, ModelResponse(parts=[TextPart("中间步骤")]),
                        ModelResponse(parts=[ToolCallPart("query", {}, "pending")])]
            state = ContextRunState(raw_history=messages[:], estimator=TokenEstimator.conservative())
            comp = DeepAgentContextCompaction(context_window_tokens=100_000, max_output_tokens=256)
            with patch.object(DeepAgentContextCompaction, "_single_summary", AsyncMock(return_value="旧任务已完成")):
                projected, _ = await comp._compact_projection(messages, SimpleNamespace(),
                    SimpleNamespace(model_request_parameters=None), 80_000, state,
                    require_summary=True, emit_deterministic=False, reserve_answer_request=False)
            self.assertIn(protected, projected)
            self.assertEqual(projected[-3:], messages[-3:])
            self.assertIs(projected[-3], protected)
            if isinstance(protected.parts[0], UserPromptPart):
                self.assertFalse(extract_anchor_protected_context(projected))

    async def test_live_raw_refresh_preserves_latest_user_and_commits_before_return(self) -> None:
        messages = [ModelRequest(parts=[UserPromptPart("原始要求：禁止写入")])]
        for index in range(5):
            messages.extend([ModelResponse(parts=[ToolCallPart("query", {}, str(index))]),
                             ModelRequest(parts=[ToolReturnPart("query", "结果" * 2500, str(index))])])
        state = ContextRunState(raw_history=messages[:1], estimator=TokenEstimator.conservative())
        state.refresh_raw_history = AsyncMock(return_value=messages[:])
        committed = []
        async def persist(projected):
            self.assertTrue(state.telemetry.summary_applied)
            self.assertTrue(state.telemetry.candidate_valid)
            self.assertEqual(extract_anchor_protected_context(projected)["latest_user_content"], ["原始要求：禁止写入"])
            committed.append(projected)
            return projected
        state.commit_projection = persist
        comp = DeepAgentContextCompaction(context_window_tokens=100_000, max_output_tokens=256)
        with patch.object(DeepAgentContextCompaction, "_single_summary", AsyncMock(return_value="继续核对结果")):
            projected, _ = await comp._compact_projection(messages, SimpleNamespace(),
                SimpleNamespace(model_request_parameters=None), 80_000, state,
                require_summary=True, emit_deterministic=False, reserve_answer_request=False)
        state.refresh_raw_history.assert_awaited_once()
        self.assertEqual(committed, [projected])
        self.assertEqual(state.raw_history, messages)

    async def test_failed_persistence_never_installs_an_uncommitted_candidate(self) -> None:
        original = [ModelRequest(parts=[UserPromptPart("原文" * 5000)])]
        state = ContextRunState(raw_history=original, estimator=TokenEstimator.conservative())
        state.commit_projection = AsyncMock(side_effect=RuntimeError("database failed"))
        state.finish_compaction = AsyncMock()
        comp = DeepAgentContextCompaction(context_window_tokens=100_000, max_output_tokens=256)
        candidate = [_summary_anchor("摘要", ())]
        with patch.object(DeepAgentContextCompaction, "_summarize_projection", AsyncMock(return_value=candidate)):
            with self.assertRaisesRegex(SummaryUnavailable, "compaction_persistence_failed"):
                await comp._compact_projection(original, SimpleNamespace(), SimpleNamespace(model_request_parameters=None),
                    80_000, state, require_summary=True, emit_deterministic=False, reserve_answer_request=False)
        state.finish_compaction.assert_awaited_once_with("failed", "compaction_persistence_failed")
        self.assertFalse(state.telemetry.candidate_valid)
        self.assertFalse(state.telemetry.summary_applied)

    async def test_oversized_result_recovers_exact_middle_value_by_real_call_id(self) -> None:
        from deep_agent.agent.history_tools import build_history_toolset
        result = "头" * 20_000 + "精确收入=87123.45万元" + "尾" * 20_000
        call_id = "stored-query-87123"
        messages = [ModelResponse(parts=[ToolCallPart("query", {"period": "2026"}, call_id)]),
                    ModelRequest(parts=[ToolReturnPart("query", result, call_id)])]
        prompt, records = _prepare_summary_prompt(messages, None, 256, 8_000, TokenEstimator.conservative())
        self.assertNotIn("87123.45万元", prompt)
        recovery = records[0]["recovery"]
        self.assertEqual(recovery, {"tool": "read_conversation_event", "call_id": call_id})
        row = SimpleNamespace(public_dict=lambda: {"payload": {"message": {"parts": [{"content": result}]}}})
        store = SimpleNamespace(read_authorized_events=AsyncMock(return_value={"items": [row], "next_sequence": None}))
        toolset = build_history_toolset(store)
        ctx = SimpleNamespace(deps=SimpleNamespace(agent_id="agent", user="owner", session_id="session"))
        arguments = {key: value for key, value in recovery.items() if key != "tool"}
        fragments = []
        while True:
            page = await toolset.tools[recovery["tool"]].function(ctx, **arguments)
            fragments.append(page["content"] or json.dumps(page["events"], ensure_ascii=False))
            if not page["has_more"]:
                break
            arguments = page["next_read"]
        recovered = json.loads("".join(fragments))
        self.assertEqual(recovered[0]["payload"]["message"]["parts"][0]["content"], result)
        self.assertTrue(all(call.kwargs["call_id"] == call_id and call.kwargs["user_id"] == "owner"
                            for call in store.read_authorized_events.await_args_list))

    async def test_summary_retries_only_transient_status_and_accounts_for_failed_requests(self) -> None:
        from pydantic_ai.exceptions import ModelAPIError
        for statuses, expected_calls, failure in (([429], 2, None), ([400], 1, "summary_provider_error"),
                                                   ([503, 503], 2, "summary_provider_error")):
            calls = []
            async def respond(messages, info):
                calls.append(True)
                if len(calls) <= len(statuses):
                    error = ModelHTTPError(statuses[len(calls) - 1], "fixture", "temporary failure")
                    error.response = SimpleNamespace(headers={"Retry-After": "0.001"})
                    raise error
                return ModelResponse(parts=[TextPart("交接：继续分析")])
            state = ContextRunState(raw_history=[], estimator=TokenEstimator.conservative())
            state.persist_summary_usage = AsyncMock(side_effect=RuntimeError("usage sink unavailable"))
            ctx = RunContext(deps=SimpleNamespace(), model=FunctionModel(respond), usage=RunUsage())
            comp = DeepAgentContextCompaction(context_window_tokens=100_000, max_output_tokens=256)
            if failure:
                with self.assertRaisesRegex(SummaryUnavailable, failure):
                    await comp._single_summary([ModelRequest(parts=[UserPromptPart("历史")])], None, ctx, 256,
                                               state, reserve_answer_request=False)
            else:
                result = await comp._single_summary([ModelRequest(parts=[UserPromptPart("历史")])], None, ctx, 256,
                                                    state, reserve_answer_request=False)
                self.assertEqual(result, "交接：继续分析")
                state.persist_summary_usage.assert_awaited_once()
            self.assertEqual(len(calls), expected_calls)
            self.assertEqual(ctx.usage.requests, expected_calls)
        self.assertTrue(is_provider_context_overflow(ModelAPIError("fixture", "input too long")))
        self.assertFalse(is_provider_context_overflow(ModelAPIError("fixture", "rate limited")))

    async def test_summary_timeout_cancels_provider_task_and_emits_heartbeat(self) -> None:
        stopped = asyncio.Event()
        async def respond(messages, info):
            try:
                await asyncio.sleep(60)
            finally:
                stopped.set()
        state = ContextRunState(raw_history=[], estimator=TokenEstimator.conservative(), event_queue=asyncio.Queue())
        ctx = RunContext(deps=SimpleNamespace(), model=FunctionModel(respond), usage=RunUsage())
        comp = DeepAgentContextCompaction(context_window_tokens=100_000, max_output_tokens=256, summary_timeout=.025)
        with patch("deep_agent.agent.context.compaction._SUMMARY_HEARTBEAT_SECONDS", .005):
            with self.assertRaisesRegex(SummaryUnavailable, "summary_timeout"):
                await comp._single_summary([ModelRequest(parts=[UserPromptPart("history")])], None, ctx, 256,
                                           state, reserve_answer_request=False)
        self.assertTrue(stopped.is_set())
        self.assertEqual(ctx.usage.requests, 1)
        self.assertGreater(state.event_queue.qsize(), 2)

    async def test_application_and_external_cancellation_never_fall_back(self) -> None:
        from pydantic_ai.exceptions import RunCancelled
        for external in (False, True):
            started = asyncio.Event()
            stopped = asyncio.Event()
            async def respond(messages, info):
                started.set()
                try:
                    if not external:
                        raise RunCancelled("user cancelled")
                    await asyncio.sleep(60)
                finally:
                    stopped.set()
            state = ContextRunState(raw_history=[], estimator=TokenEstimator.conservative())
            ctx = RunContext(deps=SimpleNamespace(), model=FunctionModel(respond), usage=RunUsage())
            comp = DeepAgentContextCompaction(context_window_tokens=100_000, max_output_tokens=256)
            task = asyncio.create_task(comp._single_summary([ModelRequest(parts=[UserPromptPart("history")])], None,
                    ctx, 256, state, reserve_answer_request=False))
            await started.wait()
            if external:
                task.cancel()
            with self.assertRaises(asyncio.CancelledError if external else RunCancelled):
                await task
            self.assertTrue(stopped.is_set())

    async def test_pressure_failure_falls_back_only_while_original_request_fits(self) -> None:
        from deep_agent.agent.context.compaction import ContextWindowExceededError
        from pydantic_ai.exceptions import RunCancelled
        original = [ModelRequest(parts=[UserPromptPart("旧任务")]), ModelResponse(parts=[TextPart("历史" * 1000)]),
                    ModelRequest(parts=[UserPromptPart("继续")])]
        for reason, occupancy, limit, failure in (("summary_timeout", 9500, 10000, None),
              ("summary_provider_error", 11000, 10000, SummaryUnavailable),
              ("cancelled", 9500, 10000, RunCancelled)):
            state = ContextRunState(raw_history=original[:], estimator=TokenEstimator.conservative())
            state.finish_compaction = AsyncMock()
            comp = DeepAgentContextCompaction(context_window_tokens=100_000, max_output_tokens=256)
            ctx = SimpleNamespace(deps=SimpleNamespace(context_state=state))
            request = SimpleNamespace(messages=original[:], model_request_parameters=None)
            budget = SimpleNamespace(enabled=True, estimated_request_tokens=occupancy, usable_request_tokens=limit, trigger_tokens=9000)
            error = RunCancelled("cancelled") if reason == "cancelled" else SummaryUnavailable(reason)
            with patch("deep_agent.agent.context.compaction.build_context_budget", return_value=budget), \
                 patch.object(DeepAgentContextCompaction, "_compact_projection", AsyncMock(side_effect=error)):
                if failure:
                    with self.assertRaises(failure):
                        await comp.before_model_request(ctx, request)
                else:
                    await comp.before_model_request(ctx, request)
                    self.assertEqual(request.messages, original)
                    self.assertEqual(state.telemetry.strategy, "original_history_fallback")
            self.assertFalse(state.telemetry.candidate_valid)
            state.finish_compaction.assert_awaited_once()

    async def test_truncated_summary_keeps_response_telemetry(self) -> None:
        def respond(messages, info):
            return ModelResponse(parts=[TextPart("## 1. Primary Request and Intent")],
                                 finish_reason="length", model_name="summary-test")
        model = FunctionModel(respond)
        state = ContextRunState(raw_history=[], estimator=TokenEstimator.conservative())
        ctx = RunContext(deps=SimpleNamespace(), model=model, usage=RunUsage())
        compaction = DeepAgentContextCompaction(context_window_tokens=100_000, max_output_tokens=4096)
        with self.assertRaisesRegex(SummaryUnavailable, "summary_truncated"):
            await compaction._single_summary(
                [ModelRequest(parts=[UserPromptPart(content="fixture")])], None,
                ctx, 256, state, reserve_answer_request=False,
            )
        self.assertEqual(state.summary_call["finish_reason"], "length")
        self.assertEqual(state.summary_call["max_output_tokens"], 256)
        self.assertIsNotNone(state.summary_call["usage"])


    async def test_deepseek_summary_sends_real_output_limit_without_main_thinking_change(self) -> None:
        import httpx2
        from pydantic_ai import Agent
        from pydantic_ai.providers.deepseek import DeepSeekProvider
        from pydantic_ai.models.openai import OpenAIChatModel
        from deep_agent.models.provider_registry import deepseek_chat_profile

        requests = []
        def transport(request):
            requests.append(json.loads(request.content))
            return httpx2.Response(200, json={
                "id": "summary-response", "object": "chat.completion", "created": 0,
                "model": "deepseek-flash", "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": valid_summary()}}],
            })
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
            model = OpenAIChatModel("deepseek-flash",
                provider=DeepSeekProvider(api_key="test-key", http_client=client),
                profile=deepseek_chat_profile("deepseek-flash"))
            state = ContextRunState(raw_history=[], estimator=TokenEstimator.conservative())
            ctx = RunContext(deps=SimpleNamespace(), model=model, usage=RunUsage())
            compaction = DeepAgentContextCompaction(context_window_tokens=100_000, max_output_tokens=4096)
            await compaction._single_summary(
                [ModelRequest(parts=[UserPromptPart(content="销售收入360成本230")])], None,
                ctx, 864, state, reserve_answer_request=False,
            )
            await Agent(model).run("main chat", model_settings={"max_tokens": 1})
        self.assertEqual(requests[0]["max_tokens"], 864)
        self.assertNotIn("max_completion_tokens", requests[0])
        self.assertEqual(requests[0]["thinking"], {"type": "disabled"})
        self.assertIn("864 tokens", requests[0]["messages"][-1]["content"])
        self.assertEqual(requests[1]["max_tokens"], 1)
        self.assertNotIn("thinking", requests[1])
        self.assertEqual(state.summary_call["max_output_tokens"], 864)

    async def test_moonshot_k26_summary_disables_native_thinking_without_changing_main_request(self) -> None:
        import httpx2
        from pydantic_ai import Agent
        from pydantic_ai.providers.moonshotai import MoonshotAIProvider
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.models.wrapper import WrapperModel
        from deep_agent.agent.context.compaction import _summary_model_settings
        requests = []
        def transport(request):
            requests.append(json.loads(request.content))
            return httpx2.Response(200, json={
                "id": "summary-response", "object": "chat.completion", "created": 0,
                "model": "kimi-k2.6", "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "保留精确数据和继续任务的下一步。"}}],
            })
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
            provider = MoonshotAIProvider(api_key="test-key", http_client=client)
            model = OpenAIChatModel("kimi-k2.6", provider=provider)
            state = ContextRunState(raw_history=[], estimator=TokenEstimator.conservative())
            ctx = RunContext(deps=SimpleNamespace(), model=WrapperModel(model), usage=RunUsage())
            comp = DeepAgentContextCompaction(context_window_tokens=100_000, max_output_tokens=2048)
            await comp._single_summary([ModelRequest(parts=[UserPromptPart("收入360万元成本230万元")])], None,
                                       ctx, 2048, state, reserve_answer_request=False)
            await Agent(model).run("继续财务分析", model_settings={"max_tokens": 4096})
            for always_thinking in ("kimi-k2-thinking", "kimi-k2.7-code", "kimi-k3"):
                settings = _summary_model_settings(OpenAIChatModel(always_thinking, provider=provider), 2048)
                self.assertNotIn("extra_body", settings)
        self.assertEqual(requests[0]["max_completion_tokens"], 2048)
        self.assertEqual(requests[0]["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", requests[0])
        self.assertNotIn("thinking", requests[1])
        self.assertEqual(requests[1]["max_completion_tokens"], 4096)

    async def test_registered_moonshot_custom_gateway_retains_explicit_provider_identity(self) -> None:
        import httpx2
        from pydantic_ai import Agent
        from pydantic_ai.providers.moonshotai import MoonshotAIProvider
        from pydantic_ai.providers.openai import OpenAIProvider
        from pydantic_ai.models.wrapper import WrapperModel
        from deep_agent.models.provider_registry import ProviderRuntimeConfig, resolve_provider_model
        from deep_agent.agent.context.compaction import _summary_model_settings
        requests = []
        def transport(request):
            requests.append(json.loads(request.content))
            return httpx2.Response(200, json={"id": "summary", "object": "chat.completion", "created": 0,
                "model": "kimi-k2.6", "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "使用最新用户修正，旧历史只是原始证据。"}}]})
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
            with patch("deep_agent.models.provider_registry.MoonshotAIProvider", side_effect=lambda **kwargs: MoonshotAIProvider(http_client=client, **kwargs)), \
                 patch("deep_agent.models.provider_registry.OpenAIProvider", side_effect=lambda **kwargs: OpenAIProvider(http_client=client, **kwargs)):
                for default in (True, False):
                    config = ProviderRuntimeConfig(id="moonshot", name="Configured Moonshot", provider_type="moonshotai",
                        use_default_base_url=default, base_url="https://private-gateway.example/v1", api_key="test")
                    model = resolve_provider_model(model_name="kimi-k2.6", provider=config)
                    self.assertEqual(model.system, "moonshotai" if default else "openai")
                    self.assertEqual(model.deepagent_provider_type, "moonshotai")
                    ctx = RunContext(deps=SimpleNamespace(), model=WrapperModel(model), usage=RunUsage())
                    state = ContextRunState(raw_history=[], estimator=TokenEstimator.conservative())
                    comp = DeepAgentContextCompaction(context_window_tokens=100_000, max_output_tokens=2048)
                    await comp._single_summary([ModelRequest(parts=[UserPromptPart("任务")])], None, ctx, 2048,
                                               state, reserve_answer_request=False)
                    await Agent(model).run("正常主请求", model_settings={"max_tokens": 4096})
                other = resolve_provider_model(model_name="kimi-k2.6", provider=ProviderRuntimeConfig(
                    id="generic", name="Generic", provider_type="openai_compatible", use_default_base_url=False,
                    base_url="https://api.moonshot.ai/v1", api_key="test"))
                self.assertNotIn("extra_body", _summary_model_settings(other, 2048))
        for index in (0, 2):
            self.assertEqual(requests[index]["thinking"], {"type": "disabled"})
            self.assertNotIn("reasoning_effort", requests[index])
            self.assertEqual(requests[index]["max_completion_tokens"], 2048)
            self.assertNotIn("thinking", requests[index + 1])
            self.assertEqual(requests[index + 1]["max_completion_tokens"], 4096)

    async def test_summary_cannot_spend_reserved_final_request_after_resume(self) -> None:
        from deep_agent.agent.deps import AgentDeps
        from deep_agent.agent.request_budget import TurnRequestBudget

        calls = []
        def respond(messages, info):
            calls.append(True)
            return ModelResponse(parts=[TextPart(valid_summary())])

        # 38 requests happened in earlier Runs; the current Run has two left.
        budget = TurnRequestBudget(UsageLimits(request_limit=40), previous_requests=38)
        deps = AgentDeps(session_id="summary-reserve")
        usage = RunUsage()
        model = FunctionModel(respond)
        state = ContextRunState(raw_history=[], estimator=TokenEstimator.conservative(),
                                usage_limits=budget.run_limits)
        ctx = RunContext(deps=deps, model=model, usage=usage, usage_limits=budget.run_limits)
        compaction = DeepAgentContextCompaction(context_window_tokens=100_000, max_output_tokens=1024)
        history = [ModelRequest(parts=[UserPromptPart(content="fixture context")])]
        await compaction._single_summary(history, None, ctx, 1024, state, reserve_answer_request=True)
        self.assertEqual(usage.requests, 1)
        with self.assertRaisesRegex(SummaryUnavailable, "request_limit_reserved"):
            await compaction._single_summary(history, None, ctx, 1024, state, reserve_answer_request=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(usage.requests, 1)

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
        self.assertGreater(state.telemetry.evidence_tokens, 1_000)
        self.assertEqual(result[1:], tail[-1:])
        protected = extract_anchor_protected_context(result[:1])
        self.assertEqual(protected, extract_protected_context(messages, cutoff=len(messages) - 1, raw_history=raw))
        self.assertEqual(protected["latest_user_content"], ["丁" * 48_000])
        self.assertEqual(state.raw_history, raw)

    async def test_budget_failure_records_candidate_without_applying_it(self) -> None:
        est = TokenEstimator.conservative()
        original = [ModelRequest(parts=[UserPromptPart(content="原文" * 2000)])]
        candidate = [_summary_anchor(valid_summary() + "甲" * 1500, ())]
        state = ContextRunState(raw_history=original, estimator=est)
        before = est.request(original)[0]
        state.telemetry.estimated_tokens_before = before
        state.telemetry.estimated_tokens_after = before
        comp = DeepAgentContextCompaction(context_window_tokens=100_000, max_output_tokens=4096)
        with patch.object(DeepAgentContextCompaction, "_summarize_projection", AsyncMock(return_value=candidate)):
            with self.assertRaisesRegex(SummaryUnavailable, "candidate_exceeds_budget"):
                await comp._compact_projection(
                    original, SimpleNamespace(), SimpleNamespace(model_request_parameters=None),
                    1000, state, require_summary=True, emit_deterministic=False,
                    reserve_answer_request=False,
                )
        self.assertEqual(state.telemetry.candidate_tokens, est.request(candidate)[0])
        self.assertEqual(state.telemetry.estimated_tokens_after, before)
        self.assertFalse(state.telemetry.summary_applied)

    async def test_single_turn_completed_tools_compact_while_pending_suffix_stays_exact(self) -> None:
        original_user = "分析2026年预算差异，金额万元，禁止写入。"
        messages = [ModelRequest(parts=[UserPromptPart(content=original_user)])]
        for index in range(8):
            messages.extend([ModelResponse(parts=[ToolCallPart("query", {"period": "2026", "index": index}, f"call-{index}")]),
                             ModelRequest(parts=[ToolReturnPart("query", "查询结果" * 700, f"call-{index}")])])
        pending = ModelResponse(parts=[ToolCallPart("query", {"period": "2026", "index": 9}, "pending")])
        messages.append(pending)
        state = ContextRunState(raw_history=messages[:], estimator=TokenEstimator.conservative())
        comp = DeepAgentContextCompaction(context_window_tokens=100_000, max_output_tokens=512)
        summarize = AsyncMock(return_value="已完成8次查询。最新请求禁止写入；当前等待第9次查询，完成后核对差异。")
        with patch.object(DeepAgentContextCompaction, "_single_summary", summarize):
            projected, after = await comp._compact_projection(messages, SimpleNamespace(),
                SimpleNamespace(model_request_parameters=None), 50_000, state,
                require_summary=True, emit_deterministic=False, reserve_answer_request=True)
        self.assertEqual(summarize.await_count, 1)
        self.assertEqual(projected[-1], pending)
        self.assertEqual(projected[1:], messages[-5:])  # two complete call/result units plus pending call
        protected = extract_anchor_protected_context(projected)
        self.assertEqual(protected["latest_user_content"], [original_user])
        self.assertEqual(protected, extract_protected_context(messages, cutoff=len(messages) - 5))
        self.assertLess(after, state.compaction_estimated_tokens_before)
        self.assertEqual(state.raw_history, messages)

    async def test_candidate_above_old_55_percent_is_accepted_under_usable_budget(self) -> None:
        est = TokenEstimator.conservative()
        original = [ModelRequest(parts=[UserPromptPart(content="甲" * 8_000)])]
        candidate = [_summary_anchor("乙" * 6_000, ())]
        state = ContextRunState(raw_history=original, estimator=est)
        comp = DeepAgentContextCompaction(context_window_tokens=20_000, max_output_tokens=512)
        with patch.object(DeepAgentContextCompaction, "_summarize_projection", AsyncMock(return_value=candidate)):
            projected, after = await comp._compact_projection(original, SimpleNamespace(),
                SimpleNamespace(model_request_parameters=None), 10_000, state,
                require_summary=True, emit_deterministic=False, reserve_answer_request=False)
        self.assertGreater(after, 5_500)
        self.assertLess(after, 10_000)
        self.assertEqual(projected, candidate)

    async def test_no_reclaim_does_not_install_candidate(self) -> None:
        original = [ModelRequest(parts=[UserPromptPart(content="短请求")])]
        candidate = [_summary_anchor("比原文更长的摘要" * 20, ())]
        state = ContextRunState(raw_history=original, estimator=TokenEstimator.conservative())
        comp = DeepAgentContextCompaction(context_window_tokens=100_000, max_output_tokens=512)
        with patch.object(DeepAgentContextCompaction, "_summarize_projection", AsyncMock(return_value=candidate)):
            with self.assertRaisesRegex(SummaryUnavailable, "no_context_reclaimed"):
                await comp._compact_projection(original, SimpleNamespace(),
                    SimpleNamespace(model_request_parameters=None), 80_000, state,
                    require_summary=True, emit_deterministic=False, reserve_answer_request=False)
        self.assertEqual(state.raw_history, original)

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
