from __future__ import annotations

import asyncio
import hashlib
import io
import zipfile
import json
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from loguru import logger

from deep_agent.documents.export import DocumentExportManager
from deep_agent.files.storage import FileStorePermissionError, LocalFileStore, PlatformFileStore, StoredFile
from deepfos_platform.errors.classes import APIStatusCodeError, APIStatusNotTrue
from deepfos_platform.lib.httpcli import AioHttpCli


HEADERS = {"user": "fixture-user", "app": "fixture-app", "space": "fixture-space"}
PAYLOAD = json.dumps({
    "schema_version": "document-evidence-v2", "document_id": "doc_fixture",
    "status": "partial", "pages": [{"page_number": 1, "blocks": [
        {"block_id": "doc_fixture:p1:b1", "content": "样例合同 💡 1,234.50", "bbox": [1, 2, 3, 4]},
    ]}],
}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

_payload_zip = io.BytesIO()
with zipfile.ZipFile(_payload_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    archive.writestr("evidence.json", PAYLOAD)
PAYLOAD = _payload_zip.getvalue()


class ControlledPlatformStore(PlatformFileStore):
    def __init__(self):
        self.calls = 0
        self.error = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.cancelled = asyncio.Event()
        self.arguments = None

    async def upload_once(self, **kwargs):
        self.calls += 1
        self.arguments = kwargs
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        if self.error:
            raise self.error
        return StoredFile("L_fixture", kwargs["file_name"], len(kwargs["data"]), "DL", kwargs["mime"])

    async def upload(self, **kwargs):
        raise AssertionError("Evidence must not use a retrying platform upload")

    async def register_download(self, **kwargs):
        raise AssertionError("Evidence is not registered in Download Center")


class DocumentExportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = DocumentExportManager()
        self.store = ControlledPlatformStore()

    async def asyncTearDown(self):
        await self.manager.aclose()

    async def export(self, payload=PAYLOAD, **kwargs):
        return await self.manager.ensure(
            "doc_fixture", payload, file_store=kwargs.pop("file_store", self.store),
            request_headers=kwargs.pop("request_headers", HEADERS), **kwargs,
        )

    async def test_read_only_peek_never_serializes_or_uploads(self):
        self.assertEqual(self.manager.peek("doc_fixture").status, "not_requested")
        self.assertIsNone(self.manager.peek("doc_fixture").file)
        self.assertEqual(self.store.calls, 0)

    async def test_singleflight_and_later_pages_reuse_exact_file(self):
        self.store.release.clear()
        producer = AsyncMock(return_value=PAYLOAD)
        calls = [asyncio.create_task(self.export(producer)) for _ in range(8)]
        await self.store.started.wait()
        self.store.release.set()
        results = await asyncio.gather(*calls)
        again = await self.export(lambda: self.fail("Do not regenerate or OCR for a later page"))
        self.assertTrue(all(result == again for result in results))
        self.assertEqual(self.store.calls, 1)
        producer.assert_awaited_once()
        self.assertEqual(again.status, "saved")
        self.assertEqual(again.file.storage, "platform")
        self.assertEqual(again.file.file_id, "L_fixture")
        self.assertEqual(again.file.byte_size, len(PAYLOAD))
        self.assertEqual(again.file.sha256, hashlib.sha256(PAYLOAD).hexdigest())
        self.assertEqual(again.file.schema_version, "document-evidence-v2")
        self.assertEqual(self.store.arguments["data"], PAYLOAD)
        self.assertEqual(again.file.mime, "application/zip")
        self.assertEqual(again.file.file_name, "doc_fixture-evidence.zip")
        self.assertEqual(self.store.arguments["request_headers"], HEADERS)
        self.assertEqual(self.manager.peek("doc_fixture"), again)

    async def test_cancelled_waiter_does_not_cancel_other_waiter(self):
        self.store.release.clear()
        first = asyncio.create_task(self.export())
        second = asyncio.create_task(self.export())
        await self.store.started.wait()
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertFalse(self.store.cancelled.is_set())
        self.store.release.set()
        self.assertEqual((await second).status, "saved")
        self.assertEqual(self.store.calls, 1)

    async def test_cancel_run_stops_remote_wait_without_retry(self):
        self.store.release.clear()
        waiting = asyncio.create_task(self.export())
        await self.store.started.wait()
        await self.manager.aclose()
        with self.assertRaises(asyncio.CancelledError):
            await waiting
        self.assertTrue(self.store.cancelled.is_set())
        self.assertEqual(self.store.calls, 1)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            await self.export()

    async def test_known_rejection_and_unknown_network_result_are_sticky_and_safe(self):
        for error, status in [
            (FileStorePermissionError("secret=do-not-return"), "failed"),
            (APIStatusNotTrue("files/upload", "secret=do-not-return"), "failed"),
            (APIStatusCodeError("files/upload", 413), "failed"),
            (APIStatusCodeError("files/upload", 429), "failed"),
            (APIStatusCodeError("files/upload", 503), "unknown"),
            (TimeoutError("secret=do-not-return"), "unknown"),
            (OSError("https://example.invalid/file?token=do-not-return"), "unknown"),
        ]:
            with self.subTest(error=type(error).__name__, status=status):
                async with _manager() as manager:
                    store = ControlledPlatformStore()
                    store.error = error
                    first = await manager.ensure("doc_x", PAYLOAD, file_store=store, request_headers=HEADERS)
                    store.error = None
                    second = await manager.ensure("doc_x", PAYLOAD, file_store=store, request_headers=HEADERS)
                    self.assertEqual(first.status, status)
                    self.assertEqual(first, second)
                    self.assertIsNone(first.file)
                    self.assertEqual(store.calls, 1)
                    self.assertNotIn("do-not-return", first.model_dump_json())
                    if status == "unknown":
                        self.assertFalse(manager.reset_failed("doc_x"))

    async def test_explicit_host_recovery_reuses_existing_bytes(self):
        self.store.error = FileStorePermissionError("no")
        self.assertEqual((await self.export()).status, "failed")
        self.store.error = None
        self.assertTrue(self.manager.reset_failed("doc_fixture"))
        self.assertEqual((await self.export()).status, "saved")
        self.assertEqual(self.store.calls, 2)
        self.assertFalse(self.manager.reset_failed("doc_fixture"))

    async def test_size_identity_store_and_serialization_rejections_never_upload(self):
        async def bad_producer():
            raise ValueError("private full OCR response")

        cases = [
            {"file_store": None},
            {"request_headers": {}},
            {"payload": bad_producer},
            {"payload": "not bytes"},
        ]
        for index, parameters in enumerate(cases):
            async with _manager() as manager:
                result = await manager.ensure(
                    f"doc_{index}", parameters.get("payload", PAYLOAD),
                    file_store=parameters.get("file_store", self.store),
                    request_headers=parameters.get("request_headers", HEADERS),
                )
                self.assertEqual(result.status, "failed")
                self.assertIsNone(result.file)
                self.assertNotIn("private", result.model_dump_json())
        with patch("deep_agent.documents.export.max_upload_bytes", return_value=len(PAYLOAD) - 1):
            result = await self.export()
            self.assertEqual(result.error.code, "file_too_large")
        self.assertEqual(self.store.calls, 0)

    async def test_missing_receipt_and_mismatched_storage_do_not_publish_a_file_id(self):
        for receipt in [
            StoredFile("", "evidence.json", len(PAYLOAD), "DL"),
            StoredFile("local_wrong_backend", "evidence.json", len(PAYLOAD), "DL"),
            StoredFile("L_valid", "evidence.json", len(PAYLOAD) + 1, "DL"),
            StoredFile("L_valid", "/Users/private/contract.json", len(PAYLOAD), "DL"),
        ]:
            async with _manager() as manager:
                with patch.object(self.store, "upload_once", new=AsyncMock(return_value=receipt)) as upload:
                    result = await manager.ensure("doc_x", PAYLOAD, file_store=self.store, request_headers=HEADERS)
                    repeated = await manager.ensure("doc_x", PAYLOAD, file_store=self.store, request_headers=HEADERS)
                    self.assertEqual(result.status, "unknown")
                    self.assertIsNone(result.file)
                    self.assertEqual(result, repeated)
                    upload.assert_awaited_once()

    async def test_changed_identity_cannot_reuse_successful_reference(self):
        saved = await self.export()
        changed = await self.export(request_headers={**HEADERS, "user": "someone-else"})
        self.assertEqual(changed.error.code, "identity_changed")
        self.assertIsNone(changed.file)
        self.assertEqual(self.manager.peek("doc_fixture"), saved)
        self.assertEqual(self.store.calls, 1)

    async def test_read_only_existing_reference_checks_identity_and_store(self):
        self.assertEqual(self.manager.peek_scoped("doc_fixture", file_store=None, request_headers={}).status, "not_requested")
        saved = await self.export()
        current = self.manager.peek_scoped("doc_fixture", file_store=self.store, request_headers=HEADERS)
        self.assertEqual(current, saved)
        for store, headers in ((self.store, {**HEADERS, "user": "another-user"}),
                               (ControlledPlatformStore(), HEADERS), (None, HEADERS)):
            result = self.manager.peek_scoped("doc_fixture", file_store=store, request_headers=headers)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.error.code, "identity_changed")
            self.assertIsNone(result.file)
        self.assertEqual(self.store.calls, 1)

    async def test_local_roundtrip_hash_schema_permissions_and_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete_document_export_") as path:
            store = LocalFileStore(path)
            await store.initialize()
            result = await self.export(file_store=store)
            self.assertEqual(result.status, "saved")
            self.assertEqual(result.file.storage, "local")
            downloaded = await store.download(result.file.file_id, HEADERS)
            self.assertEqual(downloaded, PAYLOAD)
            self.assertEqual(hashlib.sha256(downloaded).hexdigest(), result.file.sha256)
            self.assertEqual(json.loads(zipfile.ZipFile(io.BytesIO(downloaded)).read("evidence.json"))["schema_version"], result.file.schema_version)
            self.assertEqual(json.loads(zipfile.ZipFile(io.BytesIO(downloaded)).read("evidence.json"))["status"], "partial")
            with self.assertRaises(FileStorePermissionError):
                await store.download(result.file.file_id, {"user": "different-user"})
            with sqlite3.connect(store.metadata_path) as connection:
                self.assertEqual(connection.execute("select count(*) from files").fetchone()[0], 1)
                self.assertEqual(connection.execute("select count(*) from download_records").fetchone()[0], 0)
            await self.manager.aclose()
            self.assertEqual(await store.download(result.file.file_id, HEADERS), PAYLOAD)

    async def test_run_cleanup_joins_local_write_thread(self):
        started, release = threading.Event(), threading.Event()

        class SlowLocalStore(LocalFileStore):
            def _upload_sync(self, *args, **kwargs):
                started.set()
                if not release.wait(5):
                    raise TimeoutError("Test did not release local writer")
                return super()._upload_sync(*args, **kwargs)

        with tempfile.TemporaryDirectory(prefix="TMP_to_delete_document_export_") as path:
            store = SlowLocalStore(path)
            await store.initialize()
            waiting = asyncio.create_task(self.export(file_store=store))
            close = None
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                close = asyncio.create_task(self.manager.aclose())
                await asyncio.sleep(0.01)
                self.assertFalse(close.done())
            finally:
                release.set()
                if close is not None:
                    await close
            result = await waiting
            self.assertEqual(result.status, "saved")
            self.assertEqual(await store.download(result.file.file_id, HEADERS), PAYLOAD)


class _manager:
    async def __aenter__(self):
        self.value = DocumentExportManager()
        return self.value

    async def __aexit__(self, *args):
        await self.value.aclose()


class PlatformEvidenceUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.response_mode = "success"
        app = web.Application()
        app.router.add_post("/files/upload", self.handle)
        app.router.add_post("/redirect-target", self.redirect_target)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        self.base = f"http://127.0.0.1:{self.site._server.sockets[0].getsockname()[1]}"
        self.discovery = patch(
            "deep_agent.files.storage._SingleAttemptPlatformFile.get_base_url",
            new=AsyncMock(return_value=(None, None, self.base + "/files")),
        )
        self.discovery.start()

    async def asyncTearDown(self):
        self.discovery.stop()
        await AioHttpCli.close_current()
        await self.runner.cleanup()

    async def handle(self, request):
        multipart = await request.multipart()
        part = await multipart.next()
        body = bytes(await part.read())
        self.requests.append({"query": dict(request.query), "headers": dict(request.headers), "file_name": part.filename, "body": body})
        if self.response_mode == "disconnect":
            request.transport.close()
            return web.Response()
        if self.response_mode == "reject":
            return web.json_response({"status": False, "message": "secret=unsafe"}, status=403)
        if self.response_mode == "invalid":
            return web.Response(text="not-json secret=unsafe")
        if self.response_mode == "redirect":
            raise web.HTTPTemporaryRedirect(location=self.base + "/redirect-target")
        return web.json_response({"status": True, "data": {
            "id": "L_http_fixture", "fileName": part.filename,
            "fileSize": -1 if self.response_mode == "negative_size" else len(body),
            "fileType": "DL", "url": "",
        }})

    async def redirect_target(self, request):
        self.fail("Upload must not be replayed at a redirect destination")

    async def test_real_multipart_one_request_and_no_private_sdk_logging(self):
        logs = []
        token = logger.add(lambda value: logs.append(str(value)), level="DEBUG")
        try:
            async with _manager() as manager:
                result = await manager.ensure(
                    "doc_http", PAYLOAD, file_store=PlatformFileStore(),
                    request_headers={**HEADERS, "authorization": "Bearer isolated-secret-fixture"},
                )
            self.assertEqual(result.status, "saved")
            self.assertEqual(result.file.file_id, "L_http_fixture")
        finally:
            logger.remove(token)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]["body"], PAYLOAD)
        self.assertEqual(self.requests[0]["query"], {"fileType": "DL"})
        self.assertEqual(self.requests[0]["file_name"], "doc_http-evidence.zip")
        self.assertEqual(self.requests[0]["headers"]["Authorization"], "Bearer isolated-secret-fixture")
        output = "\n".join(logs)
        self.assertNotIn("isolated-secret-fixture", output)
        self.assertNotIn("样例合同", output)

    async def test_accepted_then_disconnected_and_bad_response_never_resubmit(self):
        for mode, status in [("disconnect", "unknown"), ("invalid", "unknown"),
                             ("negative_size", "unknown"), ("reject", "failed"), ("redirect", "unknown")]:
            with self.subTest(mode=mode):
                self.response_mode = mode
                self.requests.clear()
                store = PlatformFileStore()
                async with _manager() as manager:
                    first = await manager.ensure("doc_http", PAYLOAD, file_store=store, request_headers=HEADERS)
                    second = await manager.ensure("doc_http", PAYLOAD, file_store=store, request_headers=HEADERS)
                    self.assertEqual(first.status, status)
                    self.assertEqual(first, second)
                    self.assertIsNone(first.file)
                    self.assertEqual(len(self.requests), 1)
                    self.assertNotIn("unsafe", first.model_dump_json())


if __name__ == "__main__":
    unittest.main()
