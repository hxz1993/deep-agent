from __future__ import annotations

import json
import unittest
from decimal import Decimal

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
    SummaryUnavailable,
    _deterministic_context_evidence_text,
    _formatted_history,
    _protected_messages,
    _raw_prefix_for_visible_cutoff,
    _safe_boundaries,
    _prune_tool_results,
    _jsonable_usage,
    is_provider_context_overflow,
    summary_contents,
    validate_summary_markdown,
)


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


if __name__ == "__main__":
    unittest.main()
