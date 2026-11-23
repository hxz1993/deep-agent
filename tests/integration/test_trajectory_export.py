from __future__ import annotations

import asyncio
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import httpx
from fastapi import FastAPI
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolReturnPart, UserPromptPart
from sqlalchemy import update

from deep_agent.db.serialization import StoredUserPromptPart
from deep_agent.agent.active_runs import ActiveRunRegistry
from deep_agent.api.v1.admin import resolve_console_admin_access
from deep_agent.api.v1.sessions import router
from deep_agent.api.v1.diagnostic_sessions import router as diagnostic_router
from deep_agent.core.context import get_app_context
from deep_agent.db.models import ChatSessionEvent
from deep_agent.files.storage import LocalFileStore, FileStoreError
from deep_agent.security import ConsoleAdminAccess
from deep_agent.services import trajectory_export as exports
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class TrajectoryExportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, self.factory = await make_sqlite_stores()
        self.store = self.stores.coordinator
        self.agent_id = await seed_default_agent(self.stores)
        self.directory = tempfile.TemporaryDirectory(prefix="TMP_to_delete_trajectory_tests-")
        self.files = LocalFileStore(Path(self.directory.name) / "files")
        await self.files.initialize()
        self.active = ActiveRunRegistry()
        self.ctx = SimpleNamespace(store=self.store, stores=self.stores, db_session_factory=self.factory,
                                   active_runs=self.active, file_store=self.files)
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api/v1")
        self.app.include_router(diagnostic_router, prefix="/api/v1")
        self.app.dependency_overrides[get_app_context] = lambda: self.ctx
        self.admin = True
        self.app.dependency_overrides[resolve_console_admin_access] = lambda: ConsoleAdminAccess(
            allowed=self.admin, roles=("enterprise_admin",) if self.admin else ())
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.url = "/api/v1/sessions/s1/trajectory-export"
        self.headers = {"user": "owner"}
        self.trace = {"format": "deepagent-session-trajectory", "version": 1, "session_id": "s1", "events": []}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.cleanup()
        await self.engine.dispose()

    async def uploaded(self, name="source.txt", data=b"source", user="owner"):
        return await self.files.upload(file_type=FILE_TYPE_ATTACHMENT, file_name=name, mime="text/plain", data=data,
                                       request_headers={"user": user})

    async def history(self, attachments=(), artifacts=(), *, user="owner", run="r1", terminal="completed"):
        content = [{"kind": "attachment-ref", "id": file.id, "name": file.file_name, "mime": file.mime} for file in attachments]
        messages = [ModelRequest(parts=[StoredUserPromptPart(["question", *content])], run_id=run)]
        if artifacts:
            messages.append(ModelRequest(parts=[ToolReturnPart("run_code", {"result": "done"}, "ptc-call",
                metadata={"generated_files": list(artifacts)})], run_id=run))
        messages.append(ModelResponse(parts=[TextPart("answer")], run_id=run))
        await self.store.append("s1", messages, agent_id=self.agent_id, user_id=user,
                                run_id=run, turn_id=run, terminal_status=terminal)

    async def preview(self, *, url=None, headers=None):
        return await self.client.get((url or self.url) + "/preview", headers=headers or self.headers)

    async def export(self, preview=None, *, include=True, url=None, headers=None, trace=None):
        if preview is None:
            preview = (await self.preview(url=url, headers=headers)).json()
        return await self.client.post((url or self.url) + f"?include_files={'true' if include else 'false'}",
            headers=headers or self.headers, json={"trajectory": trace or self.trace,
                "event_head_sequence": preview.get("event_head_sequence", 0),
                "snapshot_digest": preview.get("snapshot_digest")})

    async def test_json_and_real_zip_preserve_trace_and_all_trusted_file_types(self):
        source = await self.uploaded("../同名.txt")
        output = await self.uploaded("../同名.txt", b"generated")
        await self.history([source], [{"file_id": output.id, "file_name": output.file_name, "byte_size": output.file_size}])
        preview = (await self.preview()).json()
        self.assertEqual((preview["file_count"], preview["upload_count"], preview["artifact_count"]), (2, 1, 1))
        self.assertEqual(preview["unknown_size_count"], 0)
        self.assertEqual(preview["known_size_bytes"], 15)
        plain = await self.export(preview, include=False)
        self.assertEqual(plain.json(), self.trace)
        response = await self.export(preview)
        self.assertEqual(response.status_code, 200, response.text[:100] if response.status_code != 200 else "")
        self.assertEqual(response.headers["X-Deep-Agent-Export-Status"], "complete")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(json.loads(archive.read("trajectory.json")), self.trace)
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(len(manifest["files"]), 2)
            for item in manifest["files"]:
                self.assertNotIn("..", Path(item["path"]).parts)
                self.assertEqual(hashlib.sha256(archive.read(item["path"])).hexdigest(), item["sha256"])
        self.assertFalse(exports._GATE.locked())

    async def test_untrusted_trace_ids_do_not_authorize_files_and_duplicate_ids_are_deduped(self):
        source, secret = await self.uploaded(), await self.uploaded("secret.txt", b"secret", user="other")
        await self.history([source, source])
        self.trace["events"] = [{"result": {"file_id": secret.id}, "url": "https://example.com/picture.png"}]
        response = await self.export()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            records = json.loads(archive.read("manifest.json"))["files"]
            self.assertEqual([item["file_id"] for item in records], [source.id])
        self.assertEqual((await self.preview()).json()["file_count"], 1)

    async def test_owner_auth_and_administrator_local_download(self):
        source = await self.uploaded()
        await self.history([source])
        self.assertEqual((await self.client.get(self.url + "/preview")).status_code, 401)
        self.assertEqual((await self.preview(headers={"user": "other"})).status_code, 404)
        admin_url = "/api/v1/diagnostics/sessions/s1/trajectory-export"
        response = await self.export(url=admin_url, headers={"user": "admin"})
        self.assertEqual(response.headers["X-Deep-Agent-Export-Included-Files"], "1")
        self.admin = False
        self.assertEqual((await self.preview(url=admin_url, headers={"user": "admin"})).status_code, 403)

    async def test_platform_download_keeps_caller_and_unknown_sizes(self):
        calls = []
        class Platform:
            async def download_bounded(self, file_id, headers, *, max_bytes):
                calls.append((file_id, dict(headers), max_bytes))
                return b"platform"
        self.ctx.file_store = Platform()
        await self.history([SimpleNamespace(id="platform-id", file_name="image.png", mime="image/png")])
        admin_url = "/api/v1/diagnostics/sessions/s1/trajectory-export"
        result = (await self.preview(url=admin_url, headers={"user": "admin"})).json()
        self.assertEqual(result["unknown_size_count"], 1)
        self.assertEqual(result["known_size_bytes"], 0)
        response = await self.export(result, url=admin_url, headers={"user": "admin", "Authorization": "Bearer test-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls[0][1]["user"], "admin")
        self.assertEqual(calls[0][1]["authorization"], "Bearer test-token")

    async def test_missing_or_denied_file_is_partial_not_a_fake_complete_export(self):
        source, lost = await self.uploaded(), await self.uploaded("lost.txt", b"gone")
        await self.history([source, lost])
        await self.files.delete(lost.id, self.headers)
        response = await self.export()
        self.assertEqual(response.headers["X-Deep-Agent-Export-Status"], "partial")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["skipped_files"], 1)
            self.assertEqual(manifest["files"][1]["reason"], "unavailable")

    async def test_running_and_in_place_history_change_reject_zip(self):
        await self.history()
        baseline = (await self.preview()).json()
        async with self.factory() as db:
            await db.execute(update(ChatSessionEvent).where(ChatSessionEvent.session_id == "s1",
                ChatSessionEvent.event_type == "assistant/message").values(status="running"))
            await db.commit()
        self.assertEqual((await self.preview()).status_code, 409)
        self.assertEqual((await self.export(baseline)).status_code, 409)
        self.assertEqual((await self.export(baseline, include=False)).status_code, 200)
        async with self.factory() as db:
            await db.execute(update(ChatSessionEvent).where(ChatSessionEvent.session_id == "s1",
                ChatSessionEvent.event_type == "assistant/message").values(status="interrupted"))
            await db.commit()
        self.assertEqual((await self.export(baseline)).status_code, 409)
        self.assertEqual((await self.export()).status_code, 200)

    async def test_other_worker_incremental_assistant_without_terminal_is_still_running(self):
        await self.store.append("s1", [ModelRequest(parts=[UserPromptPart("question")], run_id="live")],
            agent_id=self.agent_id, user_id="owner", run_id="live", turn_id="live")
        answer = ModelResponse(parts=[TextPart("still generating")], run_id="live")
        await self.store.append_message_parts("s1", messages=[answer], agent_id=self.agent_id,
            user_id="owner", run_id="live", turn_id="live")
        events = await self.store.list_all_events("s1")
        self.assertIsNone(events[-1].status)
        self.assertEqual((await self.preview()).status_code, 409)
        await self.store.append_message_parts("s1", messages=[answer], agent_id=self.agent_id,
            user_id="owner", run_id="live", turn_id="live", terminal_status="interrupted")
        self.assertEqual((await self.preview()).status_code, 200)

    async def test_limits_body_and_worker_concurrency(self):
        one, two = await self.uploaded(), await self.uploaded("two.txt")
        await self.history([one, two])
        with patch.object(exports, "MAX_FILES", 1):
            self.assertFalse((await self.preview()).json()["can_export"])
            self.assertEqual((await self.export()).status_code, 413)
        with patch.object(exports, "MAX_TRACE_BYTES", 20):
            self.assertEqual((await self.export(include=False)).status_code, 413)
        exports._GATE.acquire()
        try:
            self.assertEqual((await self.export()).status_code, 429)
        finally:
            exports._GATE.release()

    async def test_timeout_closes_partial_archive_and_cancel_releases_job(self):
        await self.history([SimpleNamespace(id="slow", file_name="slow.txt", mime="text/plain")])
        started = asyncio.Event()
        class Slow:
            async def download_bounded(self, *_args, **_kwargs):
                started.set()
                await asyncio.sleep(10)
        self.ctx.file_store = Slow()
        with patch.object(exports, "TIMEOUT_SECONDS", .5):
            response = await self.export()
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else "")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(json.loads(archive.read("manifest.json"))["files"][0]["reason"], "timeout")
        started.clear()
        pending = asyncio.create_task(self.export())
        await started.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertFalse(exports._GATE.locked())

    async def test_handler_budget_bounds_request_body_and_fatal_zip_timeout(self):
        await self.history()
        class SlowRequest:
            async def stream(self):
                await asyncio.sleep(10)
                yield b"{}"
        with patch.object(exports, "TIMEOUT_SECONDS", .01):
            with self.assertRaises(Exception) as timed_out:
                await exports.export_response(self.ctx, SlowRequest(), "s1", "owner", include_files=True)
        self.assertEqual(timed_out.exception.status_code, 504)
        self.assertFalse(exports._GATE.locked())
        original = exports._write_entry
        def expire_during_write(*args, **kwargs):
            # Exhaust the shared deadline before starting a member. A partial
            # file cannot be listed as successful in a valid-looking archive.
            return original(*args[:-1], exports.time.monotonic() - 1, **kwargs)
        with patch.object(exports, "_write_entry", expire_during_write):
            response = await self.export()
        self.assertEqual(response.status_code, 504)
        self.assertFalse(exports._GATE.locked())

    async def test_zip_thread_cancellation_waits_before_cleanup(self):
        began, finish = threading.Event(), threading.Event()
        stopped = threading.Event()
        def work():
            began.set()
            finish.wait(2)
        task = asyncio.create_task(exports._thread(work, stop=stopped))
        await asyncio.to_thread(began.wait, 1)
        task.cancel()
        await asyncio.sleep(.01)
        self.assertFalse(task.done())
        finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stopped.is_set())

    async def test_read_only_preview_allows_only_explicit_export_posts(self):
        from deep_agent.local_dev import LocalIdentity, LocalIdentityAdapter, _read_only_diagnostic_post
        await self.history()
        for path in ["/api/v1/sessions/s1/trajectory-export", "/api/v1/diagnostics/sessions/s1/trajectory-export"]:
            self.assertTrue(_read_only_diagnostic_post(path))
        for path in ["/api/v1/sessions/s1/cancel", "/api/v1/sessions/s1/trajectory-export/delete", "/api/v1/chat"]:
            self.assertFalse(_read_only_diagnostic_post(path))
        adapter = LocalIdentityAdapter(self.app, LocalIdentity(user="owner"), read_only_preview=True)
        preview = (await self.preview()).json()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=adapter), base_url="http://test") as client:
            response = await client.post(self.url + "?include_files=true", json={"trajectory": self.trace,
                "event_head_sequence": preview["event_head_sequence"], "snapshot_digest": preview["snapshot_digest"]})
        self.assertEqual(response.status_code, 200)
        operation = self.app.openapi()["paths"][self.url.replace("s1", "{session_id}")]["post"]
        schema = operation["requestBody"]
        self.assertIn("snapshot_digest", schema["content"]["application/json"]["schema"]["properties"])
        self.assertIn("application/zip", operation["responses"]["200"]["content"])
        self.assertIn("X-Deep-Agent-Export-Status", operation["responses"]["200"]["headers"])

    async def test_response_send_failure_closes_iterator_before_deleting_archive(self):
        await self._response_cleanup_case(cancel=False)

    async def test_response_transfer_cancel_closes_iterator_before_releasing_job(self):
        await self._response_cleanup_case(cancel=True)

    async def _response_cleanup_case(self, *, cancel):
        temporary = tempfile.TemporaryDirectory(prefix="TMP_to_delete_trajectory_response-")
        path = Path(temporary.name) / "trace.zip"
        path.write_bytes(b"archive bytes")
        cleaned = []
        def cleanup():
            self.assertIsNone(response.body_iterator.ag_frame)
            self.assertTrue(path.exists())
            temporary.cleanup()
            cleaned.append(True)
        response = exports.ArchiveResponse(path, temporary, cleanup, {})
        sending = asyncio.Event()
        async def send(message):
            if message["type"] == "http.response.body":
                sending.set()
                if cancel:
                    await asyncio.sleep(10)
                else:
                    raise OSError("closed")
        async def receive():
            await asyncio.sleep(10)
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}}
        task = asyncio.create_task(response(scope, receive, send))
        await sending.wait()
        if cancel:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        else:
            with self.assertRaises(Exception):
                await task
        self.assertEqual(cleaned, [True])

    async def test_overflow_and_document_evidence_are_collected_from_metadata_only(self):
        overflow, evidence = await self.uploaded("overflow.txt"), await self.uploaded("evidence.json")
        await self.store.append("s1", [ModelRequest(parts=[UserPromptPart("question")], run_id="r1"),
            ModelRequest(parts=[ToolReturnPart("parse_document", {"file_id": "untrusted"}, "call",
                metadata={"overflow_handle": overflow.id, "overflow_bytes": overflow.file_size,
                          "document_evidence_file": {"file_id": evidence.id, "file_name": evidence.file_name,
                                                     "byte_size": evidence.file_size}})], run_id="r1"),
            ModelResponse(parts=[TextPart("done")], run_id="r1")], agent_id=self.agent_id, user_id="owner",
            run_id="r1", turn_id="r1", terminal_status="completed")
        response = await self.export()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            records = json.loads(archive.read("manifest.json"))["files"]
            self.assertEqual({item["file_id"] for item in records}, {overflow.id, evidence.id})
            self.assertEqual([item["category"] for item in records], ["artifact", "artifact"])

    async def test_temporary_cleanup_error_still_releases_worker_gate(self):
        await self.history()
        real_temporary = tempfile.TemporaryDirectory(prefix="TMP_to_delete_cleanup_error-")
        def broken_cleanup():
            raise OSError("cleanup failed")
        temporary = SimpleNamespace(name=real_temporary.name, cleanup=broken_cleanup)
        try:
            with patch.object(exports.tempfile, "TemporaryDirectory", return_value=temporary):
                with self.assertRaises(OSError):
                    await self.export()
            self.assertFalse(exports._GATE.locked())
        finally:
            real_temporary.cleanup()
