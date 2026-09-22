from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from deep_agent.agent.context.budget import build_context_budget
from deep_agent.api.v1.sessions import context_status_from_data


class ContextStatusPresentationTests(unittest.TestCase):
    def _status(self, event_budget=None):
        event = None if event_budget is None else SimpleNamespace(
            event_id="compaction-1", updated_at=datetime.now(timezone.utc),
            payload={"budget": event_budget, "source": {"through_sequence": 7}},
        )
        return context_status_from_data("session-1", {
            "budget": build_context_budget([], context_window_tokens=16000, max_output_tokens=2000),
            "compaction": event, "raw_history": [], "context_window_tokens": 16000,
            "max_output_tokens": 2000, "manual_compaction_available": True,
            "manual_compaction_reason": "available",
        }).model_dump(mode="json")

    def test_request_comparison_is_separate_from_legacy_history_value(self):
        status = self._status({"estimated_before": 8000, "estimated_after": 3000,
                               "estimated_tokens_before": 11000, "estimated_tokens_after": 4500,
                               "estimate_scope": "full_model_request"})
        self.assertEqual(status["compaction_estimated_tokens"], 3000)
        self.assertEqual(status["compaction_estimated_tokens_before"], 11000)
        self.assertEqual(status["compaction_estimated_tokens_after"], 4500)
        self.assertEqual(status["compaction_estimate_scope"], "full_model_request")

    def test_manual_comparison_preserves_history_only_scope(self):
        status = self._status({"estimated_tokens_before": 11000, "estimated_tokens_after": 4500,
                               "estimate_scope": "history_only"})
        self.assertEqual(status["compaction_estimated_tokens_before"], 11000)
        self.assertEqual(status["compaction_estimate_scope"], "history_only")

    def test_old_event_uses_both_old_values_without_mixing_scopes(self):
        status = self._status({"estimated_before": 8000, "estimated_after": 3000,
                               "estimated_tokens_before": 11000})
        self.assertEqual(status["compaction_estimated_tokens_before"], 8000)
        self.assertEqual(status["compaction_estimated_tokens_after"], 3000)
        self.assertEqual(status["compaction_estimate_scope"], "history_only")

    def test_no_compaction_remains_unknown(self):
        status = self._status()
        self.assertIsNone(status["compaction_estimated_tokens_before"])
        self.assertIsNone(status["compaction_estimated_tokens_after"])
        self.assertIsNone(status["compaction_estimate_scope"])
