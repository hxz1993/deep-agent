"""Real-store regression for upload defaults, hot updates and upload-time enforcement."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from sqlalchemy import event, func, select

from deep_agent.api.v1.attachment_upload import router as settings_router
from deep_agent.api.v1.uploads import router as upload_router
from deep_agent.core.context import get_app_context
from deep_agent.db.factory import create_engine
from deep_agent.db.models import GlobalSetting
from deep_agent.db.stores.global_setting import GlobalSettingStore
from deep_agent.files.storage import StoredFile
from deep_agent.files.upload_settings import (
    UPLOAD_SETTING_KEY, UploadSettings, UploadSettingsError,
    load_upload_settings, read_upload_settings,
)
from deep_agent.security import ConsoleAdminAuthorizer


class UploadSettingsResolutionTests(unittest.IsolatedAsyncioTestCase):
    def test_absent_and_partial_rows_use_per_field_defaults_without_mutation(self):
        self.assertEqual(read_upload_settings(None), UploadSettings(20, 10))
        for value, expected in (({}, (20, 10)), ({"max_size_mb": 50}, (50, 10)),
                                ({"max_count": 15}, (20, 15))):
            with self.subTest(value=value):
                row = SimpleNamespace(version=1, value=value.copy())
                self.assertEqual(read_upload_settings(row), UploadSettings(*expected))
                self.assertEqual(row.value, value)

    def test_all_integer_boundaries_are_accepted_and_byte_unit_is_binary(self):
        for size in (1, 20, 200):
            for count in (1, 5, 10, 20):
                with self.subTest(size=size, count=count):
                    policy = read_upload_settings(SimpleNamespace(version=1, value={"max_size_mb": size, "max_count": count}))
                    self.assertEqual(policy.max_bytes, size * 1024 * 1024)
                    self.assertEqual(policy.to_stored_value(), {"max_size_mb": size, "max_count": count})

    def test_invalid_values_never_become_defaults_or_coerced_values(self):
        for field, invalids in (("max_size_mb", (0, 201, -1, 1.5, "20", True, False, None, [], {})),
                                ("max_count", (0, 21, -1, 5.5, "10", True, False, None, [], {}))):
            for invalid in invalids:
                with self.subTest(field=field, invalid=invalid), self.assertRaises(UploadSettingsError):
                    read_upload_settings(SimpleNamespace(version=1, value={field: invalid}))
        for row in (SimpleNamespace(version=2, value={}), SimpleNamespace(version=1, value=[]),
                    SimpleNamespace(version=1, value=None)):
            with self.subTest(row=row), self.assertRaises(UploadSettingsError):
                read_upload_settings(row)

    async def test_legacy_environment_cannot_change_missing_row_defaults(self):
        class EmptyStore:
            async def get(self, key):
                self_key.append(key)
                return None
        self_key = []
        with patch.dict(os.environ, {"DEEP_AGENT_UPLOAD_MAX_SIZE_MB": "199", "DEEP_AGENT_UPLOAD_MAX_COUNT": "19"}):
            self.assertEqual(await load_upload_settings(EmptyStore()), UploadSettings(20, 10))
        self.assertEqual(self_key, [UPLOAD_SETTING_KEY])

    async def test_unavailable_store_and_database_failure_are_not_missing_configuration(self):
        with self.assertRaises(UploadSettingsError):
            await load_upload_settings(None)
        class BrokenStore:
            async def get(self, key):
                raise RuntimeError("isolated database is unavailable")
        with self.assertRaisesRegex(RuntimeError, "database is unavailable"):
            await load_upload_settings(BrokenStore())


class _RecordingFileStore:
    """A FileStore boundary that retains only hashes and sizes, not large fixture bytes."""
    def __init__(self):
        self.uploaded = []

    async def upload(self, *, file_type, file_name, mime, data, request_headers):
        file_id = f"fixture-file-{len(self.uploaded) + 1}"
        self.uploaded.append({"id": file_id, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                              "owner": request_headers.get("user")})
        return StoredFile(file_id, file_name, len(data), file_type, mime)


class _HoldingStore(GlobalSettingStore):
    def __init__(self, factory):
        super().__init__(factory)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.exclusive = None

    @asynccontextmanager
    async def _write(self, *, exclusive=False):
        self.exclusive = exclusive
        async with super()._write(exclusive=exclusive) as db:
            self.entered.set()
            await self.release.wait()
            yield db


class AttachmentUploadSettingsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="TMP_to_delete_attachment_upload_")
        url = f"sqlite+aiosqlite:///{Path(self.directory.name) / 'settings.db'}"
        self.engine, self.factory = create_engine(url)
        self.other_engine, self.other_factory = create_engine(url)
        async with self.engine.begin() as connection:
            await connection.run_sync(GlobalSetting.__table__.create)
        self.store = GlobalSettingStore(self.factory)
        self.other_store = GlobalSettingStore(self.other_factory)
        self.file_store = _RecordingFileStore()
        async def checker(user, headers):
            return user == "enterprise-admin"
        self.context = SimpleNamespace(stores=SimpleNamespace(global_setting=self.store),
            file_store=self.file_store, console_admin_authorizer=ConsoleAdminAuthorizer(checker))
        self.app = FastAPI()
        self.app.include_router(settings_router, prefix="/api/v1")
        self.app.include_router(upload_router, prefix="/api/v1")
        self.app.dependency_overrides[get_app_context] = lambda: self.context
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(self.app, raise_app_exceptions=False), base_url="http://fixture")
        self.headers = {"user": "enterprise-admin", "authorization": "Bearer fixture-token"}
        self.url = "/api/v1/global-settings/attachment-upload"
        self.tasks = []

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.client.aclose()
        await self.engine.dispose()
        await self.other_engine.dispose()
        self.directory.cleanup()

    async def save(self, size=50, count=15):
        return await self.client.put(self.url, headers=self.headers,
            json={"value": {"max_size_mb": size, "max_count": count}})

    async def test_default_get_and_capabilities_are_select_only_and_do_not_insert(self):
        statements = []
        def record(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement.lstrip().split()[0].upper())
        event.listen(self.engine.sync_engine, "before_cursor_execute", record)
        try:
            result = await self.client.get(self.url, headers=self.headers)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertIsNone(result.json()["value"])
            self.assertEqual(result.json()["effective_value"], {"max_size_mb": 20, "max_count": 10})
            capabilities = await self.client.get("/api/v1/uploads/capabilities", headers=self.headers)
            self.assertEqual(capabilities.status_code, 200, capabilities.text)
            self.assertEqual((capabilities.json()["max_size_mb"], capabilities.json()["max_count"]), (20, 10))
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", record)
        self.assertTrue(statements)
        self.assertEqual(set(statements), {"SELECT"})
        self.assertIsNone(await self.other_store.get(UPLOAD_SETTING_KEY))
        self.assertEqual(result.json()["allowed_mimes"], capabilities.json()["allowed_mimes"])

    async def test_partial_stored_config_remains_partial_and_uses_defaults(self):
        await self.store.upsert(UPLOAD_SETTING_KEY, value={"max_size_mb": 35}, version=1)
        before = await self.other_store.get(UPLOAD_SETTING_KEY)
        result = await self.client.get(self.url, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["value"], {"max_size_mb": 35})
        self.assertEqual(result.json()["effective_value"], {"max_size_mb": 35, "max_count": 10})
        after = await self.other_store.get(UPLOAD_SETTING_KEY)
        self.assertEqual(after.value, before.value)
        self.assertEqual(after.updated_at, before.updated_at)

    async def test_save_is_one_row_and_other_worker_and_capabilities_observe_each_update(self):
        for size, count in ((1, 1), (200, 20), (20, 10)):
            response = await self.save(size, count)
            self.assertEqual(response.status_code, 200, response.text)
            policy = await load_upload_settings(self.other_store)
            self.assertEqual(policy, UploadSettings(size, count))
            capabilities = await self.client.get("/api/v1/uploads/capabilities", headers=self.headers)
            self.assertEqual((capabilities.json()["max_size_mb"], capabilities.json()["max_count"]), (size, count))
        async with self.other_engine.connect() as connection:
            self.assertEqual(await connection.scalar(select(func.count()).select_from(GlobalSetting)), 1)

    async def test_invalid_save_bodies_never_replace_valid_row(self):
        await self.save(50, 15)
        values = [{"max_size_mb": value, "max_count": 10} for value in (0, 201, 1.5, "20", True, None)]
        values += [{"max_size_mb": 20, "max_count": value} for value in (0, 21, 5.5, "10", True, None)]
        values += [{"max_size_mb": 20}, {"max_count": 10}, {"max_size_mb": 20, "max_count": 10, "unknown": 1}]
        for value in values:
            with self.subTest(value=value):
                response = await self.client.put(self.url, headers=self.headers, json={"value": value})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual((await self.other_store.get(UPLOAD_SETTING_KEY)).value,
                                 {"max_size_mb": 50, "max_count": 15})

    async def test_nonadmin_and_unauthenticated_settings_are_rejected_without_writes(self):
        for method in ("get", "put"):
            kwargs = {} if method == "get" else {"json": {"value": {"max_size_mb": 50, "max_count": 15}}}
            response = await getattr(self.client, method)(self.url,
                headers={"user": "ordinary-user", "authorization": "Bearer fixture-token"}, **kwargs)
            self.assertEqual(response.status_code, 403, response.text)
            response = await getattr(self.client, method)(self.url, **kwargs)
            self.assertEqual(response.status_code, 401, response.text)
        self.assertIsNone(await self.other_store.get(UPLOAD_SETTING_KEY))

    async def test_http_and_sse_chat_count_reject_against_current_database_value(self):
        from deep_agent.api.v1.chat import router as chat_router, _validate_chat_body
        from deep_agent.schemas.chat import ChatRequest
        self.app.include_router(chat_router, prefix="/api/v1")
        self.context.active_runs = SimpleNamespace(generation=0)
        response = await self.save(20, 1)
        self.assertEqual(response.status_code, 200, response.text)
        body = {"message": "fixture", "agent_id": "fixture", "attachments": [{"id": "first"}, {"id": "second"}]}
        for endpoint in ("/api/v1/chat", "/api/v1/chat/stream"):
            result = await self.client.post(endpoint, headers=self.headers, json=body)
            self.assertEqual(result.status_code, 422, result.text)
            self.assertIn("At most 1 attachments", result.json()["detail"])
        await self.save(20, 20)
        config = await load_upload_settings(self.other_store)
        _validate_chat_body(ChatRequest.model_validate(body), max_count=config.max_count)

    async def test_corrupt_config_and_unavailable_store_do_not_report_default_success(self):
        await self.store.upsert(UPLOAD_SETTING_KEY, value={"max_size_mb": 201, "max_count": 10}, version=1)
        for url in (self.url, "/api/v1/uploads/capabilities"):
            result = await self.client.get(url, headers=self.headers)
            self.assertGreaterEqual(result.status_code, 500, result.text)
        self.context.stores.global_setting = None
        result = await self.client.get(self.url, headers=self.headers)
        self.assertEqual(result.status_code, 503, result.text)
        self.assertEqual((await self.other_store.get(UPLOAD_SETTING_KEY)).value["max_size_mb"], 201)

    async def test_concurrent_first_saves_are_atomic_across_independent_engines(self):
        holding = _HoldingStore(self.factory)
        first = asyncio.create_task(holding.upsert(UPLOAD_SETTING_KEY, value={"max_size_mb": 100, "max_count": 20}, version=1))
        self.tasks.append(first)
        attempted = asyncio.Event()
        def observe_begin(_connection, _cursor, statement, _parameters, _context, _many):
            if statement == "BEGIN IMMEDIATE":
                attempted.set()
        event.listen(self.other_engine.sync_engine, "before_cursor_execute", observe_begin)
        try:
            await asyncio.wait_for(holding.entered.wait(), 2)
            second = asyncio.create_task(self.other_store.upsert(UPLOAD_SETTING_KEY, value={"max_size_mb": 50, "max_count": 15}, version=1))
            self.tasks.append(second)
            await asyncio.wait_for(attempted.wait(), 2)
            self.assertFalse(second.done())
            self.assertTrue(holding.exclusive)
        finally:
            holding.release.set()
            event.remove(self.other_engine.sync_engine, "before_cursor_execute", observe_begin)
        await asyncio.wait_for(asyncio.gather(first, second), 5)
        self.assertEqual(await load_upload_settings(self.store), UploadSettings(50, 15))
        async with self.other_engine.connect() as connection:
            self.assertEqual(await connection.scalar(select(func.count()).select_from(GlobalSetting)), 1)

    async def test_save_metadata_read_failure_happens_before_write_and_preserves_existing_values(self):
        # MIME metadata is ancillary to this write. A failure must not produce a
        # 5xx response after the new limits have already been committed.
        await self.store.upsert(UPLOAD_SETTING_KEY,
            value={"max_size_mb": 35, "max_count": 12}, version=1)
        backing = self.store
        calls = []
        class MetadataReadFails:
            async def get(self, key):
                calls.append(("get", key))
                if key == "document_parsing":
                    raise RuntimeError("fixture metadata read unavailable")
                return await backing.get(key)

            async def upsert(self, *args, **kwargs):
                calls.append(("upsert", args[0]))
                return await backing.upsert(*args, **kwargs)
        self.context.stores.global_setting = MetadataReadFails()
        response = await self.save(100, 20)
        self.assertGreaterEqual(response.status_code, 500, response.text)
        self.assertEqual(calls, [("get", "document_parsing")])
        self.assertEqual((await self.other_store.get(UPLOAD_SETTING_KEY)).value,
                         {"max_size_mb": 35, "max_count": 12})

    async def test_successful_save_returns_preloaded_metadata_without_postcommit_reads(self):
        backing = self.store
        calls = []
        class MetadataBeforeCommitOnly:
            committed = False

            async def get(self, key):
                calls.append(("get", key))
                if self.committed:
                    raise RuntimeError("fixture postcommit metadata read must not occur")
                return await backing.get(key)

            async def upsert(self, *args, **kwargs):
                calls.append(("upsert", args[0]))
                row = await backing.upsert(*args, **kwargs)
                self.committed = True
                return row
        self.context.stores.global_setting = MetadataBeforeCommitOnly()
        response = await self.save(100, 20)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(calls, [("get", "document_parsing"), ("upsert", UPLOAD_SETTING_KEY)])
        self.assertEqual(response.json()["effective_value"], {"max_size_mb": 100, "max_count": 20})
        self.assertIn("application/pdf", response.json()["allowed_mimes"])
        self.assertEqual((await self.other_store.get(UPLOAD_SETTING_KEY)).value,
                         {"max_size_mb": 100, "max_count": 20})

    async def test_transaction_failure_preserves_both_previous_values(self):
        await self.save(35, 12)
        def fail_after_write(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("UPDATE GLOBAL_SETTINGS"):
                raise RuntimeError("fixture failure after SQL execution")
        event.listen(self.engine.sync_engine, "after_cursor_execute", fail_after_write)
        try:
            with self.assertRaisesRegex(RuntimeError, "after SQL"):
                await self.store.upsert(UPLOAD_SETTING_KEY, value={"max_size_mb": 100, "max_count": 20}, version=1)
        finally:
            event.remove(self.engine.sync_engine, "after_cursor_execute", fail_after_write)
        self.assertEqual(await load_upload_settings(self.other_store), UploadSettings(35, 12))

    async def test_real_pdf_larger_than_twenty_mb_uploads_only_after_limit_is_raised(self):
        # Actual PDF document with a text layer; padding is an ordinary PDF stream.
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, NameObject
        import io
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        stream = DecodedStreamObject()
        stream.set_data(b"% DeepAgent upload regression\n" + b" " * (21 * 1024 * 1024))
        page[NameObject("/Contents")] = writer._add_object(stream)
        buffer = io.BytesIO()
        writer.write(buffer)
        data = buffer.getvalue()
        self.assertGreater(len(data), 20 * 1024 * 1024)
        headers = {"user": "ordinary-user", "authorization": "Bearer fixture-token"}
        response = await self.client.post("/api/v1/uploads", headers=headers, files={"file": ("fixture.pdf", data, "application/pdf")})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.file_store.uploaded, [])
        self.assertEqual((await self.save(30, 10)).status_code, 200)
        response = await self.client.post("/api/v1/uploads", headers=headers, files={"file": ("fixture.pdf", data, "application/pdf")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.file_store.uploaded[0]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(self.file_store.uploaded[0]["owner"], "ordinary-user")
        self.assertEqual(response.json()["size"], len(data))
        self.assertEqual((await self.save(1, 10)).status_code, 200)
        # The saved reference is unchanged; a fresh upload is subject to the new policy.
        self.assertEqual(self.file_store.uploaded[0]["size"], len(data))
        rejected = await self.client.post("/api/v1/uploads", headers=headers, files={"file": ("fixture.pdf", data, "application/pdf")})
        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertEqual(len(self.file_store.uploaded), 1)

    async def test_existing_authorized_file_still_reads_in_file_tool_and_real_python_after_lowering(self):
        from pydantic_ai import ModelRetry, RunContext
        from pydantic_ai.models.test import TestModel
        from pydantic_ai.usage import RunUsage
        from deep_agent.agent.deps import AgentDeps
        from deep_agent.files import build_file_toolset, build_file_export_toolset
        from deep_agent.files.storage import LocalFileStore
        from deep_agent.sandbox.subprocess_dev.backend import SubprocessBackend
        from deep_agent.sandbox.toolset import build_python_sandbox_tool_package
        from deep_agent.schemas.attachment import AttachmentRef
        from tests.helpers.sandbox import sandbox_test_python

        local = LocalFileStore(Path(self.directory.name) / "file-store")
        await local.initialize()
        self.context.file_store = local
        headers = {"user": "ordinary-user", "authorization": "Bearer fixture-token", "app": "fixture001", "space": "fixture"}
        data = b"first-line\n" + b"line with an amount 123.45\n" * 50_000
        self.assertGreater(len(data), 1024 * 1024)
        self.assertEqual((await self.save(2, 10)).status_code, 200)
        uploaded = await self.client.post("/api/v1/uploads", headers=headers,
            files={"file": ("source.txt", data, "text/plain")})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        reference = AttachmentRef(**{name: uploaded.json()[name] for name in ("id", "name", "mime", "size")})
        self.assertEqual((await self.save(1, 10)).status_code, 200)

        def context():
            return RunContext(deps=AgentDeps(session_id="saved-file-fixture", user="ordinary-user",
                request_headers=headers, file_store=local, upload_settings_store=self.store,
                attachment_by_id={reference.id: reference}), model=TestModel(), usage=RunUsage())
        file_context = context()
        reader = (await build_file_toolset({}).get_tools(file_context))["read_file"]
        result = await reader.call_func({"resource": {"type": "attachment", "file_id": reference.id}, "limit": 1}, file_context)
        self.assertEqual(result.content, "first-line")
        self.assertTrue(result.has_more)
        self.assertEqual(await local.download(reference.id, headers), data)

        export_context = context()
        exporter = (await build_file_export_toolset().get_tools(export_context))["export_file_to_download_center"]
        registered = await exporter.call_func({"source": {"type": "platform_file", "file_id": reference.id},
            "file_name": "source.txt"}, export_context)
        self.assertEqual(registered.file_id, reference.id)
        self.assertTrue(registered.download_record_id)
        with self.assertRaises(ModelRetry):
            await exporter.call_func({"source": {"type": "content", "content": data.decode()},
                "file_name": "new-large.txt"}, context())

        backend = SubprocessBackend(python_executable=sandbox_test_python())
        try:
            python_context = context()
            python = (await build_python_sandbox_tool_package(backend, timeout_sec=30).toolset.get_tools(python_context))["execute_python"]
            computed = await python.call_func({"code": (
                "from pathlib import Path\n"
                "p = Path('inputs/source.txt')\n"
                "{'bytes': p.stat().st_size, 'first': p.open().readline().strip()}"
            ), "files": [{"file_id": reference.id, "path": "inputs/source.txt"}]}, python_context)
            self.assertEqual(computed.return_value["status"], "ok")
            self.assertEqual(computed.return_value["result"], {"bytes": len(data), "first": "first-line"})
            # A NEW Python-produced file, unlike the old authorized input, is
            # still checked against the now-lowered upload-time policy.
            with self.assertRaises(ModelRetry):
                await python.call_func({"code": (
                    "from deepagent_files import publish_path\n"
                    "p = publish_path('too-large.txt', mime='text/plain')\n"
                    "open(p, 'wb').write(b'x' * (1024 * 1024 + 1))\n"
                    "{'generated': True}"
                )}, context())
            self.assertEqual(await local.download(reference.id, headers), data)
        finally:
            await backend.aclose()

    async def test_document_export_uses_batch_limit_and_keeps_existing_export_after_lowering(self):
        from deep_agent.documents.export import DocumentExportManager
        from deep_agent.files.storage import LocalFileStore
        import io
        import zipfile
        local = LocalFileStore(Path(self.directory.name) / "evidence-store")
        await local.initialize()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("evidence.json", '{"body":"' + 'x' * (1024 * 1024) + '"}')
        payload = buffer.getvalue()
        headers = {"user": "ordinary-user", "app": "fixture001", "space": "fixture"}
        manager = DocumentExportManager()
        try:
            self.assertEqual((await self.save(2, 10)).status_code, 200)
            first_policy = await load_upload_settings(self.store)
            result = await manager.ensure("doc_saved", payload, file_store=local, request_headers=headers,
                max_bytes=first_policy.max_bytes)
            self.assertEqual(result.status, "saved")
            self.assertEqual(await local.download(result.file.file_id, headers), payload)
            self.assertEqual((await self.save(1, 10)).status_code, 200)
            current_policy = await load_upload_settings(self.other_store)
            failed = await manager.ensure("doc_new", payload, file_store=local, request_headers=headers,
                max_bytes=current_policy.max_bytes)
            self.assertEqual(failed.status, "failed")
            self.assertEqual(failed.error.code, "file_too_large")
            reused = await manager.ensure("doc_saved", lambda: self.fail("Existing export must not be regenerated"),
                file_store=local, request_headers=headers, max_bytes=current_policy.max_bytes)
            self.assertEqual(reused.file.file_id, result.file.file_id)
            self.assertEqual(await local.download(result.file.file_id, headers), payload)
        finally:
            await manager.aclose()

    async def test_upload_size_boundary_exact_and_plus_one(self):
        self.assertEqual((await self.save(1, 10)).status_code, 200)
        headers = {"user": "ordinary-user", "authorization": "Bearer fixture-token"}
        for size, expected in ((1024 * 1024, 200), (1024 * 1024 + 1, 422)):
            with self.subTest(size=size):
                response = await self.client.post("/api/v1/uploads", headers=headers,
                    files={"file": ("boundary.txt", b"x" * size, "text/plain")})
                self.assertEqual(response.status_code, expected, response.text)
        self.assertEqual(len(self.file_store.uploaded), 1)


if __name__ == "__main__":
    unittest.main()
