from __future__ import annotations

import io
import unittest

from loguru import logger
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.usage import RequestUsage
from pydantic_ai.usage import RunUsage

from deep_agent.agent.observability import (
    cache_usage_available,
    log_event,
    run_metrics,
    usage_dict,
)
from deep_agent.agent.run_identity import conversation_id_for
from deep_agent.diagnostics.text import parse_record


class RunObservabilityTests(unittest.TestCase):
    def test_missing_provider_tokens_are_unknown_but_request_counts_survive(self) -> None:
        raw = RunUsage(requests=2, tool_calls=1)
        projected = usage_dict(raw)
        self.assertEqual(projected["requests"], 2)
        self.assertEqual(projected["tool_calls"], 1)
        for key in ("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens", "cache_write_tokens"):
            self.assertIsNone(projected[key])
        self.assertEqual(raw.input_tokens, 0)
        self.assertEqual(raw.output_tokens, 0)

    def test_measured_input_preserves_zero_output(self) -> None:
        projected = usage_dict(RunUsage(requests=1, input_tokens=100, output_tokens=0))
        self.assertEqual(projected["input_tokens"], 100)
        self.assertEqual(projected["output_tokens"], 0)
        self.assertEqual(projected["total_tokens"], 100)

    def test_usage_includes_provider_cache_tokens(self) -> None:
        usage = RunUsage(
            requests=2,
            tool_calls=3,
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=40,
            cache_write_tokens=5,
        )
        projected = usage_dict(usage)
        self.assertEqual(projected["cache_read_tokens"], 40)
        self.assertEqual(projected["cache_write_tokens"], 5)
        self.assertEqual(projected["tool_calls"], 3)

    def test_text_log_preserves_facts_and_has_no_content_fields(self) -> None:
        sink = io.StringIO()
        sink_id = logger.add(sink, format="{message}")
        try:
            log_event(
                "agent_run_completed",
                conversation_id="deepagent:agent:session",
                run_id="run-1",
                input_tokens=10,
            )
        finally:
            logger.remove(sink_id)
        raw = sink.getvalue().strip()
        payload = parse_record(raw)
        self.assertIsNotNone(payload)
        self.assertFalse(raw.startswith("{"))
        self.assertEqual(payload["raw"], raw)
        self.assertEqual(payload["input_tokens"], 10)
        self.assertEqual(payload["run_id"], "run-1")
        self.assertEqual(payload["event"], "agent_run_completed")
        self.assertNotIn("prompt", payload)
        self.assertNotIn("headers", payload)
        self.assertNotIn("tool_args", payload)

    def test_conversation_id_is_scoped_by_agent_and_session(self) -> None:
        self.assertEqual(
            conversation_id_for("agent-1", "session-1"),
            "deepagent:agent-1:session-1",
        )

    def test_missing_cache_usage_is_unknown_in_run_metrics(self) -> None:
        metrics = run_metrics(
            status="completed",
            started_at=0.0,
            model_id="model",
            usage=RunUsage(input_tokens=100, output_tokens=20),
            cache_metrics_available=False,
        )
        self.assertIsNone(metrics["cache_read_tokens"])
        self.assertIsNone(metrics["cache_write_tokens"])

    def test_request_usage_preserves_cache_metric_availability(self) -> None:
        missing = ModelResponse(
            parts=[TextPart(content="ok")],
            usage=RequestUsage(input_tokens=10, output_tokens=1),
        )
        reported = ModelResponse(
            parts=[TextPart(content="ok")],
            usage=RequestUsage(
                input_tokens=10,
                output_tokens=1,
                cache_read_tokens=0,
            ),
        )
        self.assertFalse(cache_usage_available([missing]))
        self.assertTrue(cache_usage_available([reported]))
