"""Text wire format preserves facts and original display without JSON decoding."""
import unittest
from unittest.mock import patch

from deep_agent.diagnostics.schema import EVENTS, safe_fields, sanitize_record
from deep_agent.diagnostics.text import escape, unescape, format_record, parse_record
from deep_agent.diagnostics.runtime import mirror_operational


class DiagnosticsTextTests(unittest.TestCase):
    def payload(self, event="agent_tool_completed", **fields):
        return {"event": event, "timestamp": "2026-09-12T00:00:00.123+00:00", "process_id": 123,
                "source": "api", "level": "INFO", "record_id": "a" * 32, "boot_id": "b" * 32,
                "instance_id": "c" * 16, **fields}

    def test_every_existing_event_preserves_facts_and_level(self):
        for event in EVENTS:
            for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
                payload = self.payload(event, level=level, tool="execute_python", duration_ms=12.5,
                                       session_id="session-1", tool_names=["foo", "bar"], tools_omitted=0)
                line = format_record(payload)
                record = parse_record(line)
                self.assertEqual(sanitize_record(record), sanitize_record(payload))
                self.assertEqual(record["raw"], line)
                self.assertIn(record["message"], line)
                self.assertIn("tool=execute_python", record["message"])
                self.assertFalse(line.startswith("{"))
                self.assertNotIn("\n", line)

    def test_special_characters_are_reversible_and_cannot_inject_columns(self):
        original = "中文 | key=a=b\nsecond\r\n\\pipe\\|"
        self.assertEqual(unescape(escape(original)), original)
        payload = self.payload()
        with patch.dict(EVENTS, {"agent_tool_completed": original}):
            line = format_record(payload)
            self.assertNotIn("\n", line)
            self.assertEqual(parse_record(line)["summary"], original)
            self.assertEqual(parse_record(line)["event"], payload["event"])

    def test_redaction_remains_at_write_boundary(self):
        payload = self.payload(prompt="SECRET", tool="https://SECRET", model_name="model-safe",
                               api_request_id="d" * 32, tool_names=["safe", "SECRET WITH SPACE"])
        line = format_record(payload)
        self.assertNotIn("SECRET", line)
        self.assertEqual(parse_record(line)["api_request_id"], "d" * 32)
        self.assertEqual(parse_record(line)["tool_names"], ["safe"])

    def test_unknown_and_duplicate_prefix_fields_are_not_parsed(self):
        self.assertIsNone(parse_record('{"event":"agent_tool_completed"}'))
        self.assertIsNone(parse_record("a traceback"))
        self.assertIsNone(parse_record(format_record(self.payload()) + " | level=ERROR"))

    def test_operational_sink_receives_exact_same_line(self):
        line = format_record(self.payload()) + "\n"
        with patch("loguru.logger") as logger:
            mirror_operational(line.encode(), "INFO")
        logger.opt.assert_called_once_with(raw=True)
        logger.opt.return_value.log.assert_called_once_with("INFO", line)
