from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from deep_agent.diagnostics import runtime as module
from deep_agent.diagnostics.runtime import DiagnosticConfig, DiagnosticRuntime, regular_open, open_directory
from deep_agent.diagnostics.schema import sanitize_record


@unittest.skipIf(module.fcntl is None, "POSIX file locks required")
class DiagnosticRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="deep-agent-diagnostics-TMP_to_delete-")
        self.directory = Path(self.tmp.name) / "logs"
        self.writers = []

    def tearDown(self):
        for writer in self.writers:
            writer.close()
        self.tmp.cleanup()

    def writer(self, source="api", start=True, **options):
        writer = DiagnosticRuntime(DiagnosticConfig(self.directory, **options), source)
        self.writers.append(writer)
        if start:
            writer.start()
        return writer

    def drain(self, writer):
        deadline = time.monotonic() + 3
        while writer.queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(writer.queue.unfinished_tasks, 0)

    def records(self):
        return [json.loads(line) for file in self.directory.glob("*.jsonl")
                for line in file.read_text().splitlines()]

    def test_safe_record_roundtrip_and_no_arbitrary_serialization(self):
        class Dangerous:
            def __str__(self):
                raise AssertionError("must not stringify")
        writer = self.writer()
        writer.emit("agent_run_started", session_id="session-1", prompt="NEVER_LOG_TEXT",
                    headers={"authorization": "NEVER_LOG_SECRET"}, error_type=Dangerous(),
                    model_name="https://secret@example.com", duration_ms=10**999)
        self.drain(writer)
        record, = self.records()
        self.assertEqual(record["session_id"], "session-1")
        self.assertNotIn("NEVER_LOG", json.dumps(record))
        self.assertNotIn("model_name", record)
        self.assertNotIn("duration_ms", record)
        self.assertEqual(sanitize_record(record), record)
        record.update(summary="evil summary", prompt="secret")
        safe = sanitize_record(record)
        self.assertEqual(safe["summary"], "执行开始")
        self.assertNotIn("prompt", safe)
        self.assertIsNone(sanitize_record({"event": []}))

    def test_size_rotation_and_active_file_is_never_cleaned(self):
        writer = self.writer(max_file_bytes=600)
        for _ in range(5):
            writer.emit("service_started")
        self.drain(writer)
        self.assertGreater(len(list(self.directory.glob("*.jsonl"))), 1)
        active = self.directory / writer._file_name
        os.utime(active, (0, 0))
        second = self.writer(source="gateway", retention_days=1)
        second.emit("service_started")
        self.drain(second)
        self.assertTrue(active.exists())
        self.assertEqual({r["source"] for r in self.records()}, {"api", "gateway"})

    def test_retention_and_restart_use_different_boot_files(self):
        old = self.writer()
        old.emit("service_started")
        self.drain(old)
        old_file = self.directory / old._file_name
        old.close()
        os.utime(old_file, (0, 0))
        (self.directory / "unrelated.log").write_text("keep")
        current = self.writer()
        current.emit("service_started")
        self.drain(current)
        self.assertNotEqual(old.boot_id, current.boot_id)
        self.assertFalse(old_file.exists())
        self.assertTrue((self.directory / "unrelated.log").exists())

    def test_budget_full_of_active_files_drops_without_deleting(self):
        first = self.writer(max_directory_bytes=600)
        first.emit("service_started")
        self.drain(first)
        first_path = self.directory / first._file_name
        second = self.writer("gateway", max_directory_bytes=600)
        second.emit("service_started")
        self.drain(second)
        self.assertTrue(first_path.exists())
        self.assertIn("capacity_exceeded", {x["code"] for x in second.status()["issues"]})
        self.assertEqual(second.status()["dropped_records"], 1)

    def test_queue_full_and_disabled_do_not_block_caller(self):
        writer = self.writer(start=False, queue_size=1)
        writer.emit("service_started")
        writer.emit("service_started")
        self.assertEqual(writer.queue.qsize(), 1)
        self.assertEqual(writer.status()["dropped_records"], 1)
        with patch.object(module, "mirror_operational") as mirror:
            disabled = self.writer(enabled=False)
            disabled.emit("service_started")
            self.drain(disabled)
            mirror.assert_called_once()
            self.assertIsNone(disabled._dirfd)

    def test_unwritable_directory_and_recovery(self):
        self.directory.write_text("not a directory")
        writer = self.writer()
        writer.emit("service_started")
        self.drain(writer)
        self.assertFalse(writer.status()["available"])
        self.assertEqual(writer.status()["dropped_records"], 1)
        self.directory.unlink()
        writer.emit("service_started")
        self.drain(writer)
        self.assertTrue(writer.status()["available"])
        self.assertEqual(len(self.records()), 1)

    def test_directory_symlink_is_not_followed(self):
        target = Path(self.tmp.name) / "outside"
        target.mkdir()
        self.directory.symlink_to(target, target_is_directory=True)
        writer = self.writer()
        writer.emit("service_started")
        self.drain(writer)
        self.assertEqual(list(target.iterdir()), [])
        self.assertFalse(writer.status()["available"])

    def test_safe_open_rejects_link_and_preserves_external_content(self):
        self.directory.mkdir()
        target = Path(self.tmp.name) / "outside"
        target.write_text("keep")
        (self.directory / "link").symlink_to(target)
        os.link(target, self.directory / "hardlink")
        fd = open_directory(self.directory)
        try:
            for name in ("link", "hardlink", "../outside"):
                with self.assertRaises((OSError, ValueError)):
                    regular_open(fd, name, os.O_WRONLY | os.O_TRUNC)
            self.assertEqual(target.read_text(), "keep")
        finally:
            os.close(fd)

    def test_concurrent_emit_and_file_write_failure_isolated(self):
        writer = self.writer()
        with patch.object(writer, "_write", side_effect=OSError("secret failure")):
            writer.emit("service_started")
            self.drain(writer)
        self.assertNotIn("secret", json.dumps(writer.status()))
        threads = [threading.Thread(target=lambda: [writer.emit("agent_run_started") for _ in range(10)])
                   for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.drain(writer)
        records = self.records()
        self.assertEqual(len(records), 40)
        self.assertEqual(len({x["record_id"] for x in records}), 40)

    def test_shutdown_bounded_and_global_emit_failure_is_noop(self):
        writer = self.writer(start=False)
        entered, release = threading.Event(), threading.Event()
        original = writer._write
        def slow(data):
            entered.set()
            release.wait(3)
            original(data)
        with patch.object(writer, "_write", side_effect=slow):
            writer.start()
            writer.emit("service_started")
            self.assertTrue(entered.wait(2))
            try:
                started = time.monotonic()
                writer.close(.01)
                self.assertLess(time.monotonic() - started, .5)
                self.assertIn("shutdown_incomplete", {x["code"] for x in writer.status()["issues"]})
            finally:
                release.set()
                writer.close()
        with patch.object(module, "_runtime", writer), patch.object(writer, "emit", side_effect=RuntimeError):
            module.emit("service_started")

    def test_config_defaults_and_explicit_path(self):
        with patch.dict(os.environ, {"DEEP_AGENT_LOG_FILE_PATH": "/logs/deep-agent.log"}, clear=True):
            self.assertEqual(module.config_from_env().directory, Path("/logs/diagnostics"))
        with patch.dict(os.environ, {"DEEP_AGENT_DIAGNOSTICS_LOG_DIR": "data/custom"}, clear=True):
            self.assertEqual(module.config_from_env().directory, module.REPO_ROOT / "data/custom")

    def test_ui_wait_is_labeled_as_waiting_not_success(self):
        from deep_agent.agent.observability import log_event
        with patch("deep_agent.diagnostics.emit") as emit:
            log_event("agent_run_completed", status="pending", run_id="run-1")
        self.assertEqual(emit.call_args.args, ("agent_run_pending",))
        self.assertEqual(emit.call_args.kwargs["status"], "pending")

    def test_status_failure_does_not_drop_data_or_rotate_healthy_file(self):
        writer = self.writer(start=False)
        with patch.object(writer, "_publish_status", side_effect=PermissionError):
            writer.start()
            writer.emit("service_started")
            self.drain(writer)
            writer.emit("agent_run_started")
            self.drain(writer)
            self.assertEqual(len(self.records()), 2)
            self.assertEqual(writer.status()["dropped_records"], 0)
            self.assertEqual(len(list(self.directory.glob("*.jsonl"))), 1)
            self.assertIn("status_unavailable", {x["code"] for x in writer.status()["issues"]})

    def test_operational_and_diagnostic_outputs_fail_independently(self):
        writer = self.writer(start=False)
        with patch.object(module, "mirror_operational", side_effect=OSError):
            writer.start()
            writer.emit("agent_run_started")
            self.drain(writer)
            self.assertEqual(len(self.records()), 1)
        with patch.object(module, "mirror_operational") as mirror, patch.object(writer, "_write", side_effect=OSError):
            writer.emit("agent_run_completed")
            self.drain(writer)
            mirror.assert_called_once()
            self.assertEqual(json.loads(mirror.call_args.args[0])["event"], "agent_run_completed")


class DiagnosticLifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_original_lifespan_result_and_exception_are_preserved(self):
        from contextlib import asynccontextmanager
        visits = []
        @asynccontextmanager
        async def original(app):
            visits.append("startup")
            try:
                yield
            finally:
                visits.append("shutdown")
        with patch.object(module, "initialize"), patch.object(module, "shutdown"), patch.object(module, "emit") as emit:
            async with module.observe_lifespan(original, "api")(None):
                visits.append("body")
            self.assertEqual(visits, ["startup", "body", "shutdown"])
            self.assertEqual([call.args[0] for call in emit.call_args_list], ["service_starting", "service_started", "service_stopped"])
        failure = ValueError("fixture")
        @asynccontextmanager
        async def failed(app):
            raise failure
            yield
        with patch.object(module, "initialize"), patch.object(module, "shutdown"), patch.object(module, "emit") as emit:
            with self.assertRaises(ValueError) as caught:
                async with module.observe_lifespan(failed, "gateway")(None):
                    self.fail("must not start")
            self.assertIs(caught.exception, failure)
            self.assertEqual(emit.call_args.args[0], "service_initialization_failed")
