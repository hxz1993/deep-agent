from __future__ import annotations

import unittest

from pydantic_ai import ToolDenied
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from deep_agent.agent.history import repair_unprocessed_tool_calls


class RepairUnprocessedToolCallsTests(unittest.TestCase):
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

    def test_appends_tool_denied_for_dangling_call(self) -> None:
        messages = [
            ModelRequest(parts=[UserPromptPart(content="confirm?")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="confirm_action",
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


if __name__ == "__main__":
    unittest.main()
