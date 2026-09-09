from copy import deepcopy
import unittest

from pydantic_ai import ToolDenied
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

from deep_agent.conversation import project_session_events
from tests.unit.test_conversation_projector import event


class LegacyFailedToolProjectionTests(unittest.TestCase):
    def _events(self, message, status="failed"):
        return [
            event(1, "assistant/message", status=status, message=ModelResponse(parts=[
                ToolCallPart(tool_name="synthetic_confirm", args={}, tool_call_id="call-1"),
            ])),
            event(2, "tool/results", message=ModelRequest(parts=[
                ToolReturnPart(tool_name="synthetic_confirm", tool_call_id="call-1",
                               content=ToolDenied(message=message)),
            ])),
        ]

    def test_old_failed_repair_is_failed_without_changing_events(self):
        events = self._events("Tool execution failed before a result was produced")
        before = deepcopy(events)
        item = project_session_events(events).items[0]
        self.assertEqual((item.status, item.outcome), ("failed", "failed"))
        self.assertEqual(events, before)

    def test_does_not_reclassify_other_denials_or_completed_runs(self):
        for message, status in [("User declined", "failed"),
                                ("Tool execution failed before a result was produced", "completed")]:
            with self.subTest(message=message, status=status):
                item = project_session_events(self._events(message, status)).items[0]
                self.assertEqual(item.status, "completed")
