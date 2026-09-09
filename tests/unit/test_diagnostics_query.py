from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from deep_agent.api.v1.admin import require_console_admin, resolve_console_admin_access
from deep_agent.api.v1.diagnostics import router
from deep_agent.diagnostics import query
from deep_agent.security import ConsoleAdminAccess


class DiagnosticsQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix="TMP_to_delete_diagnostics_query_")
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name).resolve()
        self.config = SimpleNamespace(directory=self.directory, instance_id="a" * 16,
                                      enabled=True, retention_days=7)
        self.filters = {"start": "2026-09-08T00:00:00Z", "end": "2026-09-09T00:00:00Z"}

    def record(self, number, source="api", **extra):
        return {"event": "agent_run_started", "source": source, "level": "INFO", "process_id": 123,
                "record_id": f"{number:032x}", "boot_id": "b" * 32, "instance_id": "a" * 16,
                "timestamp": f"2026-09-08T00:00:{number:02d}Z", "session_id": "session-one", **extra}

    def write(self, records, source="api", sequence=1):
        path = self.directory / f"da-{'a' * 16}-{source}-123-{'b' * 32}-{sequence:06d}.jsonl"
        path.write_text("".join(json.dumps(record) + "\n" for record in records))
        return path

    def search(self, **kwargs):
        return query.query_logs(self.config, **self.filters, **kwargs)

    def test_merge_filters_and_safe_projection(self):
        self.write([self.record(1), self.record(3, summary="SECRET", stack="SECRET", args={"secret": 1})])
        self.write([self.record(2, "gateway"), self.record(4, "gateway")], "gateway")
        results = self.search()["items"]
        self.assertEqual([int(item["record_id"], 16) for item in results], [4, 3, 2, 1])
        self.assertNotIn("SECRET", json.dumps(results))
        self.assertEqual(len(self.search(source="api", q="执行开始")["items"]), 2)
        self.assertEqual(self.search(q=".*")["items"], [])
        self.assertEqual(self.search(session_id="another")["items"], [])

    def test_cursor_snapshot_ignores_append_and_no_duplicates(self):
        path = self.write([self.record(1), self.record(2), self.record(3)])
        first = self.search(limit=1)
        with path.open("a") as stream:
            stream.write(json.dumps(self.record(4)) + "\n")
        second = self.search(limit=1, cursor=first["next_cursor"])
        third = self.search(limit=1, cursor=second["next_cursor"])
        self.assertEqual([int(page["items"][0]["record_id"], 16) for page in (first, second, third)], [3, 2, 1])
        self.assertIsNone(third["next_cursor"])

    def test_cursor_is_constant_short_reference_for_many_files_and_retries(self):
        for number in range(1, 101):
            self.write([self.record(1), self.record(2)], sequence=number)
        first = self.search(limit=1)
        cursor = first["next_cursor"]
        self.assertEqual(len(cursor), 43)
        self.assertNotIn("jsonl", cursor)
        self.assertNotIn("session-one", cursor)
        second = self.search(limit=2, cursor=cursor)
        retried = self.search(limit=2, cursor=cursor)
        self.assertEqual(second["items"], retried["items"])
        self.assertEqual(len(second["next_cursor"]), 43)

    def test_cursor_ttl_lru_eviction_and_process_restart_are_uniform_conflicts(self):
        now = [0.0]
        cache = query._CursorCache(max_entries=2, ttl=900, clock=lambda: now[0])
        first = cache.put({"value": [1]})
        second = cache.put({"value": [2]})
        returned = cache.get(first)
        returned["value"].append(99)
        self.assertEqual(cache.get(first), {"value": [1]})
        third = cache.put({"value": [3]})
        with self.assertRaisesRegex(query.QueryError, "invalid_or_expired_cursor"):
            cache.get(second)
        self.assertEqual(cache.get(third), {"value": [3]})
        now[0] = 899
        cache.get(first)
        now[0] = 900
        for invalid in (first, third, first[:-1] + ("a" if first[-1] != "a" else "b")):
            with self.assertRaises(query.QueryError) as caught:
                cache.get(invalid)
            self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(cache.used_bytes, 0)
        with self.assertRaisesRegex(query.QueryError, "invalid_or_expired_cursor"):
            query._CursorCache().get(first)

    def test_cursor_cache_bounds_count_and_serialized_memory(self):
        cache = query._CursorCache(max_entries=128, max_bytes=16 * 1024 * 1024)
        tokens = [cache.put({"payload": "x" * 150000}) for _ in range(130)]
        self.assertLessEqual(len(cache.entries), 128)
        self.assertLessEqual(cache.used_bytes, 16 * 1024 * 1024)
        self.assertEqual(cache.used_bytes, sum(entry[2] for entry in cache.entries.values()))
        with self.assertRaisesRegex(query.QueryError, "invalid_or_expired_cursor"):
            cache.get(tokens[0])
        self.assertEqual(len(cache.get(tokens[-1])["payload"]), 150000)
        before = cache.used_bytes
        with self.assertRaisesRegex(query.QueryError, "cursor_capacity_exceeded"):
            cache.put({"payload": "x" * (16 * 1024 * 1024)})
        self.assertEqual(cache.used_bytes, before)

    def test_evicted_and_expired_pagination_return_409_without_scanning(self):
        self.write([self.record(1), self.record(2)])
        now = [0.0]
        cache = query._CursorCache(max_entries=1, ttl=10, clock=lambda: now[0])
        with patch.object(query, "_CURSORS", cache):
            first = self.search(limit=1)
            second = self.search(limit=1)
            with self.assertRaisesRegex(query.QueryError, "invalid_or_expired_cursor"):
                self.search(cursor=first["next_cursor"])
            now[0] = 10
            with self.assertRaisesRegex(query.QueryError, "invalid_or_expired_cursor"):
                self.search(cursor=second["next_cursor"])

    def test_cursor_tamper_filters_deletion_and_rotation_rejected(self):
        path = self.write([self.record(1), self.record(2)])
        cursor = self.search(limit=1)["next_cursor"]
        for changes in ({"cursor": cursor + "x"}, {"cursor": cursor, "source": "gateway"}):
            with self.assertRaises(query.QueryError) as caught:
                self.search(**changes)
            self.assertEqual(caught.exception.status_code, 409)
        path.rename(path.with_suffix(".old"))
        self.write([self.record(1), self.record(2)])
        with self.assertRaisesRegex(query.QueryError, "snapshot_changed"):
            self.search(cursor=cursor)

    def test_unknown_event_malformed_raw_and_symbols_never_exposed(self):
        path = self.write([self.record(1, event="unknown", stack="secret"), self.record(2),
                           self.record(3, duration_ms=10**500)])
        with path.open("a") as stream:
            stream.write("Traceback raw secret\n" + "x" * 100000 + "\n")
        records = self.search()["items"]
        self.assertEqual([int(item["record_id"], 16) for item in records], [3, 2])
        self.assertNotIn("duration_ms", records[0])
        other = self.directory / f"da-{'a' * 16}-api-124-{'c' * 32}-000001.jsonl"
        other.symlink_to(path)
        with self.assertRaises(query.QueryError):
            self.search()

    def test_directory_symlink_and_path_input_are_not_read(self):
        self.write([self.record(1)])
        alias = self.directory / "alias"
        alias.symlink_to(self.directory, target_is_directory=True)
        self.config.directory = alias
        with self.assertRaises(query.QueryError):
            self.search()
        self.assertFalse(query.logs_status(self.config)["available"])

    def test_oversized_line_continuation_makes_progress_without_inventing_records(self):
        path = self.write([self.record(1)])
        with path.open("a") as stream:
            stream.write(json.dumps(self.record(2)) + "x" * 500000 + "\n")
        gathered, cursor = [], None
        with patch.object(query, "MAX_SCAN_BYTES", 100000):
            for _ in range(20):
                page = self.search(cursor=cursor)
                gathered.extend(page["items"])
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            else:
                self.fail("oversized line stopped cursor progress")
        self.assertEqual([int(item["record_id"], 16) for item in gathered], [1])

    def test_limits_and_resumable_frontier(self):
        self.write([self.record(i) for i in range(1, 20)])
        self.write([self.record(i, "gateway") for i in range(20, 40)], "gateway")
        with patch.object(query, "MAX_SCAN_BYTES", 900):
            cursor, gathered = None, []
            for _ in range(100):
                page = self.search(cursor=cursor)
                self.assertLessEqual(page["scanned_bytes"], 900)
                gathered.extend(int(item["record_id"], 16) for item in page["items"])
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            else:
                self.fail("scan did not advance")
        self.assertEqual(gathered, list(range(39, 0, -1)))
        with patch.object(query, "MAX_SCAN_SECONDS", 0):
            self.assertTrue(self.search()["partial"])
        with self.assertRaises(query.QueryError):
            self.search(limit=501)
        query._SLOTS.acquire()
        query._SLOTS.acquire()
        try:
            with self.assertRaisesRegex(query.QueryError, "busy"):
                self.search()
        finally:
            query._SLOTS.release()
            query._SLOTS.release()

    def test_status_aggregates_only_safe_source_issues(self):
        path = self.directory / f"da-{'a' * 16}-gateway-123-{'b' * 32}.status.json"
        path.write_text(json.dumps({"instance_id": "a" * 16, "source": "gateway", "issues": ["write_failed", "raw secret"],
                                    "dropped_records": 4, "current_file": "/secret/path"}))
        result = query.logs_status(self.config)
        self.assertTrue(result["available"])
        self.assertEqual(result["scope"], "current_instance")
        self.assertNotIn("secret", json.dumps(result))
        self.assertIn({"source": "gateway", "code": "write_failed"}, result["issues"])

    def test_file_count_overflow_does_not_claim_subset_is_newest(self):
        self.write([self.record(1)])
        self.write([self.record(2, "gateway")], "gateway")
        with patch.object(query, "MAX_FILES", 1):
            result = self.search()
        self.assertTrue(result["partial"])
        self.assertEqual(result["items"], [])

    def test_router_admin_dependency_and_http_validation(self):
        for route in router.routes:
            self.assertIn(require_console_admin, [dependency.call for dependency in route.dependant.dependencies])
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[require_console_admin] = lambda: None
        with TestClient(app) as client, patch("deep_agent.api.v1.diagnostics._runtime", return_value=(self.config, None)):
            self.assertEqual(client.get("/api/v1/diagnostics/logs/status").status_code, 200)
            self.assertEqual(client.get("/api/v1/diagnostics/logs", params={"limit": 501}).status_code, 422)
            self.assertEqual(client.get("/api/v1/diagnostics/logs", params={"start": "invalid"}).status_code, 422)
            self.assertEqual(client.get("/api/v1/diagnostics/logs", params={"end": "0001-01-01T00:00:00Z"}).status_code, 422)
            self.assertEqual(client.get("/api/v1/diagnostics/logs", params={"cursor": "tampered"}).status_code, 409)
            self.write([self.record(1, session_id="another-users-session")])
            response = client.get("/api/v1/diagnostics/logs", params={**self.filters, "session_id": "another-users-session"})
            self.assertEqual(len(response.json()["items"]), 1)
        app.dependency_overrides.clear()
        app.dependency_overrides[resolve_console_admin_access] = lambda: ConsoleAdminAccess(allowed=False, roles=())
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/v1/diagnostics/logs").status_code, 403)
            self.assertEqual(client.get("/api/v1/diagnostics/logs/status").status_code, 403)

    def test_real_writer_files_are_read_without_event_store_or_db(self):
        from deep_agent.diagnostics.runtime import DiagnosticConfig, DiagnosticRuntime
        config = DiagnosticConfig(directory=self.directory, instance_id="a" * 16)
        writers = [DiagnosticRuntime(config, source) for source in ("api", "gateway")]
        try:
            for writer in writers:
                writer.start()
                writer.emit("agent_run_started", session_id="synthetic-session", run_id="synthetic-run")
        finally:
            for writer in writers:
                writer.close()
        result = query.query_logs(config, session_id="synthetic-session")
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual({item["source"] for item in result["items"]}, {"api", "gateway"})
        self.assertTrue(query.logs_status(config)["available"])
