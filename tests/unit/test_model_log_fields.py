"""Safe model API facts must survive logging without retaining request content."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deep_agent.diagnostics.runtime import DiagnosticConfig, DiagnosticRuntime
from deep_agent.diagnostics.query import query_logs
from deep_agent.diagnostics.text import format_record, parse_record
from deep_agent.diagnostics.schema import (
    IDENTIFIER_FIELDS, MAX_RECORD_BYTES, NUMBER_FIELDS, TOOL_LIST_BUDGET,
    TOOL_LIST_FIELDS, safe_fields, sanitize_record,
)


class ModelLogFieldsTests(unittest.TestCase):
    def facts(self):
        return {"model_api": "https://models.example.test:8443", "api_path": "/chat/completions",
                "http_method": "POST", "http_status": 400, "api_request_id": "a" * 32,
                "tool_manifest_status": "complete", "tool_names_hash": "b" * 64,
                "tool_count": 3, "unique_tool_count": 2,
                "tool_names": ["read_file", "docs_search", "docs_search"],
                "duplicate_tool_names": ["docs_search"], "added_tool_names": ["docs_search"],
                "history_tool_names": ["docs_search"], "redeclared_tool_names": ["docs_search"]}

    def test_only_allowlisted_model_facts_roundtrip_through_real_runtime(self):
        with tempfile.TemporaryDirectory(prefix="model-logs-TMP_to_delete-") as directory:
            writer = DiagnosticRuntime(DiagnosticConfig(Path(directory)), "api")
            with patch("deep_agent.diagnostics.runtime.mirror_operational"):
                try:
                    writer.start()
                    writer.emit("model_api_request", **self.facts(),
                                headers={"Authorization": "NEVER_LOG_SECRET"},
                                request_body="NEVER_LOG_PROMPT", response_body="NEVER_LOG_REPLY",
                                body={"image": "NEVER_LOG_BASE64"}, provider_request_id="NEVER_LOG_SERVER_ID")
                finally:
                    writer.close()
            lines = [line for file in Path(directory).glob("*.log") for line in file.read_text().splitlines()]
            self.assertEqual(len(lines), 1)
            self.assertNotIn("NEVER_LOG", lines[0])
            record = sanitize_record(parse_record(lines[0]))
            self.assertEqual(record["tool_names"], self.facts()["tool_names"])
            self.assertEqual(record["history_tool_names"], ["docs_search"])
            self.assertEqual(record["redeclared_tool_names"], ["docs_search"])
            self.assertEqual(record["tools_omitted"], 0)
            self.assertEqual(sanitize_record(record), record)
            self.assertLessEqual(len(lines[0].encode()) + 1, MAX_RECORD_BYTES)
            now = datetime.now(timezone.utc)
            result = query_logs(writer.config, start=now - timedelta(minutes=5),
                                end=now + timedelta(minutes=1), q="docs_search")
            self.assertEqual(result["items"][0]["duplicate_tool_names"], ["docs_search"])
            self.assertEqual(result["items"][0]["redeclared_tool_names"], ["docs_search"])
            # Reading an edited file must apply the same boundary again.
            record.update(headers="NEVER_LOG_READ", body="NEVER_LOG_READ", summary="NEVER_LOG_READ")
            self.assertNotIn("NEVER_LOG", json.dumps(sanitize_record(record)))

    def test_origin_rejects_credentials_paths_query_fragments_and_invalid_ports(self):
        for address in ("https://user:secret@models.test", "https://models.test/key-secret",
                        "https://models.test?token=secret", "https://models.test#secret",
                        "https://models.test/", "https://models.test:99999", "https://models.test:0",
                        "https://models.test\nsecret", "file:///private", "data:secret"):
            with self.subTest(address=address):
                self.assertNotIn("model_api", safe_fields({"model_api": address}))
        for address in ("https://models.test", "http://127.0.0.1:8000", "http://[::1]:8000"):
            self.assertEqual(safe_fields({"model_api": address})["model_api"], address)
        self.assertEqual(safe_fields({"api_path": "/secret/chat/completions", "http_method": "POST secret",
                                      "http_status": 999, "api_request_id": "server-supplied-secret",
                                      "tool_manifest_status": "body secret", "tool_names_hash": "secret"}), {})

    def test_tool_lists_do_not_coerce_objects_and_reject_non_protocol_names(self):
        class Dangerous:
            def __str__(self):
                raise AssertionError("must not stringify")
            def __repr__(self):
                raise AssertionError("must not represent")
        result = safe_fields({"tool_names": ["safe_name-1", Dangerous(), {"token": "SECRET"},
                                            "<script>", "https://secret", "a" * 129, 1, "with space"],
                              "headers": Dangerous(), "body": Dangerous()})
        self.assertEqual(result, {"tool_names": ["safe_name-1"], "tools_omitted": 7})
        self.assertEqual(safe_fields({"tool_names": "SECRET", "added_tool_names": {"a": "SECRET"}}), {})
        for name in ("history_tool_names", "redeclared_tool_names"):
            with self.subTest(name=name):
                self.assertEqual(safe_fields({name: ["safe_name", Dangerous(), "https://SECRET", {"body": "SECRET"}]}),
                                 {name: ["safe_name"], "tools_omitted": 3})
                self.assertEqual(safe_fields({name: "SECRET"}), {})

    def test_large_tool_lists_keep_full_counts_hash_and_bound_record_bytes(self):
        facts = self.facts()
        names = [f"tool_{index:04d}_" + "x" * 90 for index in range(1000)]
        facts.update(tool_names=names, added_tool_names=names, duplicate_tool_names=["duplicate"],
                     history_tool_names=names, redeclared_tool_names=["redeclared"],
                     tool_count=1000, unique_tool_count=999)
        result = safe_fields(facts)
        self.assertEqual(result["tool_count"], 1000)
        self.assertEqual(result["unique_tool_count"], 999)
        self.assertEqual(result["tool_names_hash"], "b" * 64)
        self.assertEqual(result["duplicate_tool_names"], ["duplicate"])
        self.assertEqual(result["redeclared_tool_names"], ["redeclared"])
        self.assertGreater(result["tools_omitted"], 2900)
        self.assertLessEqual(len(json.dumps({key: result[key] for key in TOOL_LIST_FIELDS},
                                           separators=(",", ":")).encode()), TOOL_LIST_BUDGET)
        self.assertEqual(safe_fields(result), result)
        # Max-size existing facts must not make added lists drop a whole record.
        maximal = {**dict.fromkeys(IDENTIFIER_FIELDS, "x" * 180),
                   **dict.fromkeys(NUMBER_FIELDS, 10**15), **facts}
        bounded = safe_fields(maximal)
        # The public boundary is the actual pipe-delimited UTF-8 record, not a
        # JSON estimate plus an arbitrary envelope allowance. Scalar allowlist
        # growth may consume that estimate while the wire formatter still fits.
        line = format_record({
            **bounded, "event": "model_api_request", "source": "api", "level": "INFO",
            "timestamp": datetime.now(timezone.utc).isoformat(), "process_id": 123,
            "record_id": "a" * 32, "boot_id": "b" * 32, "instance_id": "c" * 16,
        })
        self.assertLessEqual(len(line.encode()) + 1, MAX_RECORD_BYTES)
        restored = parse_record(line)
        self.assertIsNotNone(restored)
        self.assertEqual(restored["tool_count"], 1000)
        self.assertEqual(restored["unique_tool_count"], 999)
        self.assertEqual(restored["tool_names_hash"], "b" * 64)
        for key in ("request_id", "session_id", "run_id", "tool_call_id"):
            self.assertEqual(restored[key], maximal[key])
        self.assertEqual(safe_fields(bounded), bounded)
        self.assertEqual(format_record(restored), line)


if __name__ == "__main__":
    unittest.main()
