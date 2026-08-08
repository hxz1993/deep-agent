from __future__ import annotations

import unittest

from pydantic_ai import ToolDenied
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from deep_agent.agent.history import repair_unprocessed_tool_calls


class RepairUnprocessedToolCallsTests(unittest.TestCase):
    def test_official_interrupted_tool_call_is_not_repaired(self) -> None:
        interrupted = ModelResponse(
            parts=[ToolCallPart(tool_name="slow_tool", args={}, tool_call_id="c1")],
            state="interrupted",
        )
        messages = [ModelRequest(parts=[UserPromptPart(content="hi")]), interrupted]

        repaired = repair_unprocessed_tool_calls(messages)

        self.assertIs(repaired, messages)
        self.assertEqual(len(repaired), 2)

    def test_no_op_when_balanced(self) -> None:
        messages = [
            ModelRequest(parts=[UserPromptPart(content="hi")]),
            ModelResponse(parts=[ToolCallPart(tool_name="t", args={}, tool_call_id="c1")]),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="t",
                        content={"ok": True},
                        tool_call_id="c1",
                    )
                ]
            ),
        ]
        repaired = repair_unprocessed_tool_calls(messages)
        self.assertEqual(repaired, messages)

    def test_native_call_and_return_in_same_response_are_not_repaired(self) -> None:
        messages = [
            ModelRequest(parts=[UserPromptPart(content="查找")]),
            ModelResponse(
                parts=[
                    NativeToolCallPart("native_lookup", {"query": "预算"}, "native-1"),
                    NativeToolReturnPart("native_lookup", {"ok": True}, "native-1"),
                ]
            ),
        ]

        repaired = repair_unprocessed_tool_calls(messages)

        self.assertIs(repaired, messages)

    def test_appends_tool_denied_for_dangling_call(self) -> None:
        messages = [
            ModelRequest(parts=[UserPromptPart(content="confirm?")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="builtin__confirm",
                        args={"message": "go?"},
                        tool_call_id="call-1",
                    )
                ]
            ),
        ]
        repaired = repair_unprocessed_tool_calls(messages)
        self.assertEqual(len(repaired), 3)
        denied_request = repaired[-1]
        self.assertIsInstance(denied_request, ModelRequest)
        part = denied_request.parts[0]
        self.assertIsInstance(part, ToolReturnPart)
        assert isinstance(part, ToolReturnPart)
        self.assertEqual(part.tool_call_id, "call-1")
        self.assertIsInstance(part.content, ToolDenied)

    def test_multiple_dangling_ids(self) -> None:
        messages = [
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name="a", args={}, tool_call_id="1"),
                    ToolCallPart(tool_name="b", args={}, tool_call_id="2"),
                ]
            ),
        ]
        repaired = repair_unprocessed_tool_calls(messages)
        self.assertEqual(len(repaired), 2)
        self.assertIsInstance(repaired[-1], ModelRequest)
        self.assertEqual(len(repaired[-1].parts), 2)

    def test_inserts_denied_before_later_assistant_response(self) -> None:
        """Regression: back-to-back assistant tool_calls without an intervening return."""
        messages = [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="deepcube_read",
                        args={},
                        tool_call_id="call_z",
                    )
                ]
            ),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="deepcube_read",
                        args={},
                        tool_call_id="call_csv",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="deepcube_read",
                        content="ok",
                        tool_call_id="call_csv",
                    )
                ]
            ),
            ModelResponse(parts=[]),
        ]
        repaired = repair_unprocessed_tool_calls(messages)
        self.assertEqual(len(repaired), 5)
        denied = repaired[1]
        self.assertIsInstance(denied, ModelRequest)
        assert isinstance(denied, ModelRequest)
        self.assertEqual(len(denied.parts), 1)
        part = denied.parts[0]
        assert isinstance(part, ToolReturnPart)
        self.assertEqual(part.tool_call_id, "call_z")
        self.assertIsInstance(part.content, ToolDenied)

    def test_inserts_denied_before_user_turn(self) -> None:
        messages = [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="builtin__confirm",
                        args={},
                        tool_call_id="call-1",
                    )
                ]
            ),
            ModelRequest(parts=[UserPromptPart(content="new question")]),
        ]
        repaired = repair_unprocessed_tool_calls(messages)
        self.assertEqual(len(repaired), 3)
        self.assertIsInstance(repaired[1], ModelRequest)
        denied_part = repaired[1].parts[0]
        assert isinstance(denied_part, ToolReturnPart)
        self.assertEqual(denied_part.tool_call_id, "call-1")
        self.assertIsInstance(repaired[2], ModelRequest)
        self.assertIsInstance(repaired[2].parts[0], UserPromptPart)


if __name__ == "__main__":
    unittest.main()
