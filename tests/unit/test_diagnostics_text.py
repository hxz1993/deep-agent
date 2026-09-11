"""Text wire format preserves facts and original display without JSON decoding."""
import unittest
from unittest.mock import patch

from deep_agent.diagnostics.schema import EVENTS, IDENTIFIER_FIELDS, NUMBER_FIELDS, MAX_RECORD_BYTES, safe_fields, sanitize_record
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
                self.assertNotIn("tool=execute_python", record["message"])
                self.assertIn("tool=execute_python", record["raw"])
                self.assertEqual(record["message"], record["summary"])
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

    def test_tool_model_and_run_messages_show_existing_facts(self):
        cases = [
            ("agent_tool_completed", dict(tool="execute_python", duration_ms=320, attempt=1),
             ("工具 execute_python 调用完成", "320ms", "第 1 次尝试")),
            ("agent_tool_completed", dict(tool="execute_python", result_status="error", error_code="PYTHON_EXCEPTION"),
             ("调用已返回，脚本执行失败", "PYTHON_EXCEPTION")),
            ("agent_tool_failed", dict(tool="execute_python", error_type="ValueError"),
             ("工具 execute_python 调用失败", "ValueError")),
            ("model_api_response", dict(model_name="deepseek-flash", http_method="POST",
              api_path="/responses", http_status=200, duration_ms=109),
             ("deepseek-flash", "响应头已收到", "POST /responses", "HTTP 200", "109ms")),
            ("model_request_completed", dict(model_name="deepseek-flash", request_index=2,
              input_tokens=100, output_tokens=20), ("第 2 轮", "输入 100 Token", "输出 20 Token")),
            ("agent_run_completed", dict(agent_name="日志展示验收", requests=2, tool_calls=1),
             ("Agent 日志展示验收 执行完成", "模型请求 2 次", "工具调用 1 次")),
        ]
        for event, fields, expected in cases:
            with self.subTest(event=event):
                result = parse_record(format_record(self.payload(event, session_id="hidden-id", **fields)))
                for text in expected:
                    self.assertIn(text, result["message"])
                self.assertNotIn("hidden-id", result["message"])
                self.assertEqual(result["session_id"], "hidden-id")

    def test_reader_preserves_written_message_even_if_event_template_changes(self):
        line = format_record(self.payload(tool="sample", agent_name="中文 | Agent\\name"))
        with patch.dict(EVENTS, {"agent_tool_completed": "new template"}):
            record = parse_record(line)
        self.assertIn("工具 sample 调用完成", record["summary"])
        self.assertIn("中文 | Agent\\name", record["message"])
        self.assertEqual(record["raw"], line)
        self.assertEqual(record["agent_name"], "中文 | Agent\\name")

    def test_absent_facts_and_unsafe_names_are_not_invented(self):
        for name in ("https://private", "secret=abc", "x" * 101, object()):
            result = parse_record(format_record(self.payload(agent_name=name)))
            self.assertNotIn("agent_name", result)
            self.assertNotIn("None", result["message"])
            self.assertNotIn("耗时", result["message"])
        result = parse_record(format_record(self.payload(result_status="error")))
        self.assertIn("脚本执行失败", result["message"])
        self.assertNotIn("None", result["message"])

    def test_wire_budget_preserves_scalars_and_counts_omitted_tools(self):
        fields = {key: "x" * 180 for key in IDENTIFIER_FIELDS}
        fields.update({key: 10**15 for key in NUMBER_FIELDS})
        fields.update(agent_name="中|\\" * 33, tool_names=["tool_" + str(n) * 30 for n in range(256)])
        payload = self.payload("model_api_request", **fields)
        line = format_record(payload)
        record = parse_record(line)
        self.assertLessEqual(len(line.encode()) + 1, MAX_RECORD_BYTES)
        self.assertEqual(record["raw"], line)
        for key, value in safe_fields(fields).items():
            if key not in ("tool_names", "tools_omitted"):
                self.assertEqual(record[key], value, key)
        self.assertEqual(len(record["tool_names"]) + record["tools_omitted"], 256)
        # A large translated title must not cause loss of the whole record.
        with patch.dict(EVENTS, {"service_started": "中文 | \\" * 3000}):
            line = format_record(self.payload("service_started", session_id="retained"))
            record = parse_record(line)
        self.assertLessEqual(len(line.encode()) + 1, MAX_RECORD_BYTES)
        self.assertTrue(record["message"].endswith("…"))
        self.assertEqual(record["session_id"], "retained")

    def test_operational_sink_receives_exact_same_line(self):
        line = format_record(self.payload()) + "\n"
        with patch("loguru.logger") as logger:
            mirror_operational(line.encode(), "INFO")
        logger.opt.assert_called_once_with(raw=True)
        logger.opt.return_value.log.assert_called_once_with("INFO", line)
