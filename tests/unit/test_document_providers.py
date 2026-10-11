from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx2
from pydantic_ai._ssrf import ResolvedUrl

from deep_agent.documents.models import DocumentSource, DocumentParsingError
from deep_agent.documents.providers import PROVIDERS, parse_document, _glm_pages, _paddle_pages, _result, _cpu, _data
from deep_agent.documents.settings import DocumentParsingRuntimeConfig, resolve_document_parsing_config, read_document_parsing_config
from deep_agent.api.v1.document_parsing import connection_sample, _response, _resolve, test_document_parsing_settings
from deep_agent.schemas.document_parsing import DocumentParsingSettingsWrite
from deep_agent.diagnostics.schema import safe_fields
from fastapi import FastAPI
from fastapi.testclient import TestClient
from deep_agent.api.v1 import api_router
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.security import ConsoleAdminAuthorizer
from tests.helpers.sqlite_stores import make_sqlite_stores_sync


def source():
    return DocumentSource("example.pdf", "application/pdf", b"%PDF-FAKE", 2)


class DocumentSettingsTests(unittest.TestCase):
    def test_glm_visualization_only_binds_complete_known_page_sequence(self):
        body = {"md_results": ["one", "two"], "layout_visualization": ["https://cdn.example/one", "https://cdn.example/two"]}
        pages = _glm_pages(body)
        self.assertEqual([p.native["processed_image_url"] for p in pages], body["layout_visualization"])
        self.assertEqual(pages[0].native["processed_image_source"], "glm_layout_visualization")
        self.assertNotIn("processed_image_url", _glm_pages({**body, "layout_visualization": ["https://cdn.example/one"]})[0].native)
        self.assertNotIn("processed_image_url", _glm_pages({"md_results": "unknown page", "layout_visualization": ["https://cdn.example/one"]})[0].native)
        self.assertEqual([p.content for p in pages], ["one", "two"])

    def test_paddle_processed_images_bound_within_each_batch_without_shift(self):
        def batch(images, indices):
            return {"result": {"preprocessedImages": images, "layoutParsingResults": [
                {"prunedResult": {"page_index": i}, "markdown": {"text": f"page {i}"}}
                for i in indices]}}
        rows = [batch(["https://cdn.example/one", None, "https://cdn.example/three"], [0, 1, 2]),
                batch(["https://cdn.example/wrong-length"], [3, 4]),
                batch(["https://cdn.example/six"], [5])]
        pages = _paddle_pages("\n".join(json.dumps(row) for row in rows).encode())
        self.assertEqual([p.page for p in pages], [1, 2, 3, 4, 5, 6])
        self.assertEqual([p.native.get("processed_image_url") for p in pages], [
            "https://cdn.example/one", None, "https://cdn.example/three", None, None, "https://cdn.example/six"])
        self.assertEqual(pages[0].native["processed_image_source"], "paddle_preprocessed_images")
        self.assertNotIn("processed_image_url", rows[0]["result"]["layoutParsingResults"][0])

    def test_same_provider_preserves_key_only(self):
        existing = DocumentParsingRuntimeConfig("provider", "paddle", "real-key")
        self.assertEqual(resolve_document_parsing_config({"mode": "provider", "provider": "paddle"}, existing=existing).api_key, "real-key")
        with self.assertRaises(ValueError):
            resolve_document_parsing_config({"mode": "provider", "provider": "glm"}, existing=existing)
        self.assertEqual(resolve_document_parsing_config({"mode": "unconfigured"}).to_stored_value(), {})
        self.assertNotIn("real-key", repr(existing))

    def test_masked_key_and_invalid_mode_rejected(self):
        for value in ("abc****xyz", "••••", "abc…xyz"):
            with self.assertRaises(ValueError):
                resolve_document_parsing_config({"mode": "provider", "provider": "paddle", "api_key": value})
        with self.assertRaises(ValueError):
            resolve_document_parsing_config({"mode": "unconfigured", "provider": "paddle"})
        with self.assertRaises(ValueError):
            resolve_document_parsing_config({"mode": "wrong"})

    def test_read_response_contains_no_secret_and_has_capabilities(self):
        config = read_document_parsing_config(SimpleNamespace(version=1, value={"mode": "provider", "provider": "glm", "api_key": "secret-real-key-1234"}))
        response = _response(None, config).model_dump()
        self.assertNotIn("secret-real-key-1234", json.dumps(response))
        self.assertTrue(response["value"]["api_key_set"])
        self.assertEqual([item["provider"] for item in response["providers"]], ["paddle", "glm"])
        broken = read_document_parsing_config(SimpleNamespace(version=2, value={}))
        self.assertIsNotNone(broken.configuration_error)
        broken = read_document_parsing_config(SimpleNamespace(version=1, value={"mode": "provider", "provider": "glm"}))
        self.assertEqual(broken.provider, "glm")
        self.assertIsNotNone(broken.configuration_error)

    def test_removed_supplier_is_unconfigured_without_key_leak_or_fallback(self):
        row = SimpleNamespace(version=1, value={"mode": "provider", "provider": "mineru", "api_key": "removed-private-key"})
        config = read_document_parsing_config(row)
        self.assertEqual(config.mode, "unconfigured")
        self.assertIsNone(config.provider)
        self.assertIsNone(config.api_key)
        self.assertIsNotNone(config.configuration_error)
        response = _response(row, config).model_dump()
        self.assertNotIn("removed-private-key", json.dumps(response))
        self.assertEqual(row.value["provider"], "mineru")  # Reading must not mutate persistence.
        with self.assertRaises(ValueError):
            resolve_document_parsing_config(row.value)
        with self.assertRaises(ValueError):
            DocumentParsingSettingsWrite(value=row.value)

    def test_provider_error_code_is_safe_without_obsolete_category_mapping(self):
        with self.assertRaises(DocumentParsingError) as caught:
            _data({"errorCode": "unsupported-upstream", "msg": "private body"})
        self.assertEqual(caught.exception.code, "provider_rejected")
        self.assertNotIn("private", str(caught.exception))

    def test_capabilities_are_service_specific(self):
        self.assertFalse(PROVIDERS["paddle"].supports("a.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))
        self.assertFalse(PROVIDERS["glm"].supports("a.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))
        self.assertFalse(PROVIDERS["paddle"].supports("a.webp", "image/webp"))

    def test_sample_is_one_real_pdf_page(self):
        from pypdf import PdfReader
        sample = connection_sample()
        pdf = PdfReader(io.BytesIO(sample.data))
        self.assertEqual(len(pdf.pages), 1)
        self.assertIn("123.45", pdf.pages[0].extract_text())
        self.assertEqual(len(pdf.pages[0].images), 1)
        self.assertGreater(pdf.pages[0].images[0].image.width, 1000)

    def test_diagnostics_whitelist_omits_raw_credentials_and_url(self):
        record = safe_fields({"provider_name": "paddle", "source_hash": "a" * 64, "cache_hit": True,
            "token_count": 48000, "returned_pages": 2, "has_more": False, "offset": 1,
            "document_api_path": "https://signed.example/?secret=foo", "api_key": "secret", "body": "private"})
        self.assertEqual(record["token_count"], 48000)
        self.assertTrue(record["cache_hit"])
        self.assertFalse(record["has_more"])
        self.assertNotIn("api_key", record)
        self.assertNotIn("document_api_path", record)

    def test_adaptation_diagnostics_allow_only_counts_and_safe_identifiers(self):
        record = safe_fields({"adaptation_version": "document-adaptation-v1", "source_characters": 5000,
            "adapted_characters": 1000, "native_token_count": 3000, "adapted_token_count": 500,
            "table_count": 2, "markdown_table_count": 1, "compact_html_table_count": 1,
            "adaptation_fallback_count": 0, "image_placeholder_count": 0,
            "supplemented_block_count": 3,
            "raw_html": "private contract", "api_key": "secret", "content": "contract body"},
            event="document_adaptation_completed")
        self.assertEqual(record["adaptation_version"], "document-adaptation-v1")
        self.assertEqual(record["native_token_count"], 3000)
        self.assertEqual(record["adapted_token_count"], 500)
        self.assertEqual(record["table_count"], 2)
        self.assertEqual(record["supplemented_block_count"], 3)
        self.assertNotIn("raw_html", record)
        self.assertNotIn("content", record)
        self.assertNotIn("api_key", record)


    def test_paddle_invalid_native_shapes_are_safe_errors(self):
        for result in ([], {"layoutParsingResults": None}, {"layoutParsingResults": [None]},
                       {"layoutParsingResults": [{"markdown": None}]}):
            with self.subTest(result=result), self.assertRaises(DocumentParsingError) as cm:
                _paddle_pages(json.dumps({"result": result}).encode())
            self.assertEqual(cm.exception.code, "invalid_result")

    def test_paddle_nullable_metadata_keeps_native_markdown(self):
        body = {"result": {"layoutParsingResults": [{"prunedResult": None, "markdown": {"text": "**native** 123.45"}}]}}
        pages = _paddle_pages(json.dumps(body).encode())
        self.assertEqual(pages[0].page, 1)
        self.assertEqual(pages[0].content, "**native** 123.45")

    def test_unknown_page_boundaries_do_not_fake_complete_coverage(self):
        result = _result("glm", source(), _glm_pages({"md_results": "whole document"}), {})
        self.assertIsNone(result.pages[0].page)
        self.assertEqual(result.status, "partial")
        self.assertFalse(any("缺失页：" in warning for warning in result.warnings))


class DocumentProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="TMP_to_delete-doc-protocol-")
        self.root = Path(self.directory.name)
        self.resolver = patch("deep_agent.documents.providers.validate_and_resolve_url", new=AsyncMock(return_value=
            ResolvedUrl(resolved_ip="8.8.8.8", hostname="cdn.example", port=443, is_https=True, path="/blob?signature=hidden")))
        self.resolver.start()
        self.sleeper = patch("deep_agent.documents.providers.asyncio.sleep", new=AsyncMock())
        self.sleeper.start()

    async def asyncTearDown(self):
        self.sleeper.stop()
        self.resolver.stop()
        self.directory.cleanup()

    async def run_provider(self, provider, handler, *, source_value=None):
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            return await parse_document(DocumentParsingRuntimeConfig("provider", provider, "test-key"), source_value or source(), self.root, client=client)


    async def test_signed_result_private_redirect_is_rejected_without_forwarding_key(self):
        requests = []
        public_url = ResolvedUrl(resolved_ip="8.8.8.8", hostname="cdn.example", port=443, is_https=True, path="/blob")
        async def resolve(url, **kwargs):
            if "127.0.0.1" in url:
                raise ValueError("private address")
            return public_url
        def handler(request):
            requests.append(request)
            if request.method == "POST":
                return httpx2.Response(200, json={"data": {"jobId": "task-id-1234"}})
            if request.url.host == "paddleocr.aistudio-app.com":
                return httpx2.Response(200, json={"data": {"state": "done", "resultUrl": {"jsonUrl": "https://cdn.example/result"}}})
            self.assertNotIn("authorization", request.headers)
            return httpx2.Response(302, headers={"Location": "https://127.0.0.1/internal"})
        with patch("deep_agent.documents.providers.validate_and_resolve_url", side_effect=resolve):
            with self.assertRaises(DocumentParsingError) as cm:
                await self.run_provider("paddle", handler)
        self.assertEqual(cm.exception.code, "unsafe_url")
        self.assertFalse(any(request.url.host == "127.0.0.1" for request in requests))
        self.assertFalse((self.root / "result.jsonl").exists())

    async def test_stage_timings_include_full_download_and_stay_safe(self):
        records = []
        fully_read = False
        raw = json.dumps({"result": {"layoutParsingResults": [{"markdown": {"text": "Original **Text** 123.45"}}, {"markdown": {"text": "second"}}]}}).encode()

        class DownloadStream(httpx2.AsyncByteStream):
            async def __aiter__(self):
                nonlocal fully_read
                yield raw[:20]
                # The HTTP diagnostic must include body-read time rather than
                # claim completion as soon as response headers arrive.
                from time import sleep
                await asyncio.to_thread(sleep, 0.02)
                fully_read = True
                yield raw[20:]

        def log(event, **fields):
            if event == "document_http_response" and fields.get("http_method") == "GET" and fields.get("document_api_path") == "/signed-artifact":
                self.assertTrue(fully_read)
                self.assertGreaterEqual(fields["elapsed_ms"], 10)
            records.append((event, safe_fields(fields)))

        def handler(request):
            if request.method == "POST":
                return httpx2.Response(200, json={"data": {"jobId": "timing-task"}})
            if request.url.host == "paddleocr.aistudio-app.com":
                return httpx2.Response(200, json={"data": {"state": "done", "resultUrl": {"jsonUrl": "https://cdn.example/download?secret=private-signature"}}})
            return httpx2.Response(200, stream=DownloadStream())

        with patch("deep_agent.documents.providers.emit", side_effect=log):
            result = await self.run_provider("paddle", handler)
        self.assertEqual(result.status, "complete")
        stages = {fields["phase"]: fields for event, fields in records if event == "document_parse_phase" and "elapsed_ms" in fields}
        self.assertEqual(set(stages), {"submit", "wait", "download", "parse"})
        for stage in stages.values():
            self.assertEqual(stage["status"], "completed")
            self.assertIsInstance(stage["elapsed_ms"], int)
            self.assertGreaterEqual(stage["elapsed_ms"], 0)
        serialized = json.dumps(records)
        for private in ("private-signature", "test-key", "Original **Text**", "123.45", "signature=hidden"):
            self.assertNotIn(private, serialized)

    async def test_removed_provider_cannot_submit_or_resume_network(self):
        from deep_agent.documents.providers import can_resume_document
        requests = []
        with self.assertRaises(DocumentParsingError) as caught:
            await self.run_provider("mineru", lambda request: requests.append(request))
        self.assertEqual(caught.exception.code, "unconfigured")
        self.assertEqual(requests, [])
        (self.root / "task.json").write_text(json.dumps({"provider": "mineru", "task_id": "old-task", "uploaded": True}))
        self.assertFalse(can_resume_document(self.root, DocumentParsingError("timeout", "fixture")))

    async def test_paddle_multipart_and_native_jsonl(self):
        def handler(request):
            if request.method == "POST":
                self.assertIn(b"PaddleOCR-VL-1.6", request.content)
                self.assertIn(b"optionalPayload", request.content)
                self.assertIn(b"mergeTables", request.content)
                return httpx2.Response(200, json={"data": {"jobId": "job-12345678"}})
            if request.url.host == "paddleocr.aistudio-app.com":
                return httpx2.Response(200, json={"data": {"state": "done", "resultUrl": {"jsonUrl": "https://cdn.example/jsonl"}}})
            data = {"errorCode": 0, "result": {"layoutParsingResults": [
                {"markdown": {"text": "page one **raw**"}}, {"markdown": {"text": "<table>raw2</table>"}}]}}
            return httpx2.Response(200, text=json.dumps(data))
        parsed = await self.run_provider("paddle", handler)
        self.assertEqual(parsed.pages[0].content, "page one **raw**")
        self.assertEqual(parsed.pages[1].content, "<table>raw2</table>")
        self.assertEqual(parsed.status, "complete")

    async def test_glm_data_uri_and_layout_page_content(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(request.url.path, "/api/paas/v4/layout_parsing")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "glm-ocr")
            self.assertIs(body["need_layout_visualization"], True)
            self.assertTrue(body["file"].startswith("data:application/pdf;base64,"))
            return httpx2.Response(200, json={"md_results": "native combined", "layout_details": [[{"content": "123.45"}], [{"content": "<table>7%</table>"}]], "usage": {"total_tokens": 3}})
        parsed = await self.run_provider("glm", handler)
        self.assertEqual([p.content for p in parsed.pages], ["123.45", "<table>7%</table>"])
        self.assertEqual(parsed.native["md_results"], "native combined")
        await self.run_provider("glm", handler)
        self.assertEqual(len(requests), 1)

    async def test_glm_official_file_limits_reject_before_submission(self):
        requests = []
        self.assertEqual(PROVIDERS["glm"].max_bytes, 50_000_000)
        self.assertEqual(PROVIDERS["glm"].max_pages, 100)
        cases = (
            (DocumentSource("large.pdf", "application/pdf", b"x" * 50_000_001, 1), "file_too_large"),
            (DocumentSource("large.png", "image/png; charset=binary", b"x" * 10_000_001, 1), "file_too_large"),
            (DocumentSource("long.pdf", "application/pdf", b"%PDF", 101), "too_many_pages"),
        )
        for input_source, code in cases:
            with self.subTest(code=code, mime=input_source.mime_type):
                with self.assertRaises(DocumentParsingError) as cm:
                    await self.run_provider("glm", lambda request: requests.append(request), source_value=input_source)
                self.assertEqual(cm.exception.code, code)
        self.assertEqual(requests, [])

    async def test_glm_pdf_uses_pdf_limit_instead_of_image_limit(self):
        input_source = DocumentSource("document.pdf", "application/pdf", b"x" * 10_000_001, 1)
        requests = []
        def handler(request):
            requests.append(request)
            return httpx2.Response(200, json={"md_results": ["native PDF content"]})
        result = await self.run_provider("glm", handler, source_value=input_source)
        self.assertEqual(result.pages[0].content, "native PDF content")
        self.assertEqual(len(requests), 1)

    async def test_http_error_categories_are_distinct_and_safe(self):
        for status, code in ((401, "invalid_key"), (403, "permission_denied"), (429, "rate_limited"), (415, "unsupported_format")):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as folder:
                self.root = Path(folder)
                with self.assertRaises(DocumentParsingError) as cm:
                    await self.run_provider("glm", lambda request: httpx2.Response(status, json={"error": "private-body-key"}))
                self.assertEqual(cm.exception.code, code)
                self.assertNotIn("private-body-key", str(cm.exception))

    async def test_uncertain_submission_is_never_retried_even_by_second_call(self):
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx2.ReadTimeout("secret-url", request=request)
        for _ in range(2):
            with self.assertRaises(DocumentParsingError) as cm:
                await self.run_provider("glm", handler)
            self.assertEqual(cm.exception.code, "submission_unknown")
            self.assertNotIn("secret-url", str(cm.exception))
        self.assertEqual(len(calls), 1)

    async def test_cancel_poll_retains_original_task_and_recovers(self):
        calls = []
        gate = asyncio.Event()
        ready = asyncio.Event()
        async def handler(request):
            calls.append(request)
            if request.method == "POST":
                return httpx2.Response(200, json={"data": {"jobId": "job-12345678"}})
            ready.set()
            await gate.wait()
            return httpx2.Response(200, json={"data": {"state": "failed"}})
        pending = asyncio.create_task(self.run_provider("paddle", handler))
        await ready.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        gate.set()
        with self.assertRaises(DocumentParsingError) as cm:
            await self.run_provider("paddle", handler)
        self.assertEqual(cm.exception.code, "remote_task_failed")
        self.assertEqual(len([r for r in calls if r.method == "POST"]), 1)

    async def test_safe_query_retry_does_not_resubmit(self):
        calls = []
        def handler(request):
            calls.append(request)
            if request.method == "POST":
                return httpx2.Response(200, json={"data": {"jobId": "job-12345678"}})
            if len(calls) == 2:
                return httpx2.Response(429, headers={"Retry-After": "1"})
            return httpx2.Response(200, json={"data": {"state": "failed"}})
        with self.assertRaises(DocumentParsingError):
            await self.run_provider("paddle", handler)
        self.assertEqual(len([r for r in calls if r.method == "POST"]), 1)
        self.assertEqual(len(calls), 3)

    async def test_configuration_probe_checks_actual_answer(self):
        store = SimpleNamespace(get=AsyncMock(return_value=None))
        ctx = SimpleNamespace(stores=SimpleNamespace(global_setting=store))
        body = DocumentParsingSettingsWrite(value={"mode": "provider", "provider": "glm", "api_key": "test-key"})
        result = _result("glm", connection_sample(), _glm_pages({"md_results": ["DeepAgent Document Test 123.45"]}), {})
        with patch("deep_agent.api.v1.document_parsing.parse_document", new=AsyncMock(return_value=result)):
            response = await test_document_parsing_settings(body, ctx)
        self.assertTrue(response.ok)
        result = _result("glm", connection_sample(), _glm_pages({"md_results": ["incomplete"]}), {})
        with patch("deep_agent.api.v1.document_parsing.parse_document", new=AsyncMock(return_value=result)):
            response = await test_document_parsing_settings(body, ctx)
        self.assertFalse(response.ok)
        self.assertEqual(response.error_code, "sample_mismatch")

    async def test_cancel_cpu_write_joins_worker_before_cleanup(self):
        started = threading.Event()
        release = threading.Event()
        def writer():
            started.set()
            release.wait(5)
            (self.root / "artifact").write_text("finished")
        task = asyncio.create_task(_cpu(writer))
        while not started.is_set():
            await asyncio.get_running_loop().run_in_executor(None, started.wait, 0.01)
        task.cancel()
        await asyncio.get_running_loop().run_in_executor(None, lambda: None)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual((self.root / "artifact").read_text(), "finished")


class DocumentSettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.stores, self.engine, factory = make_sqlite_stores_sync()
        async def checker(user, headers):
            return user == "enterprise-admin"
        init_context(AppContext.for_testing(stores=self.stores, db_engine=self.engine, db_session_factory=factory,
                     console_admin_authorizer=ConsoleAdminAuthorizer(checker)))
        app = FastAPI()
        app.include_router(api_router)
        self.client = TestClient(app)
        self.headers = {"user": "enterprise-admin", "authorization": "Bearer fake-token"}
        self.url = "/api/v1/global-settings/document-parsing"

    def tearDown(self):
        self.client.close()
        reset_context()
        asyncio.run(self.engine.dispose())

    def test_save_is_local_no_paid_parse_and_key_retention_clear(self):
        with patch("deep_agent.api.v1.document_parsing.parse_document", new=AsyncMock()) as parser:
            response = self.client.put(self.url, headers=self.headers, json={"value": {"mode": "provider", "provider": "paddle", "api_key": "private-real-key"}})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("private-real-key", response.text)
            parser.assert_not_awaited()
        response = self.client.put(self.url, headers=self.headers, json={"value": {"mode": "provider", "provider": "paddle"}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(asyncio.run(self.stores.global_setting.get("document_parsing")).value["api_key"], "private-real-key")
        response = self.client.put(self.url, headers=self.headers, json={"value": {"mode": "unconfigured"}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(asyncio.run(self.stores.global_setting.get("document_parsing")).value, {})

    def test_removed_saved_provider_api_is_unconfigured_without_writing_storage(self):
        stored = {"mode": "provider", "provider": "mineru", "api_key": "old-private-key"}
        asyncio.run(self.stores.global_setting.upsert("document_parsing", value=stored, version=1))
        result = self.client.get(self.url, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["value"]["mode"], "unconfigured")
        self.assertIsNone(result.json()["value"]["provider"])
        self.assertFalse(result.json()["value"]["api_key_set"])
        self.assertIsNotNone(result.json()["value"]["configuration_error"])
        self.assertNotIn("old-private-key", result.text)
        self.assertEqual(asyncio.run(self.stores.global_setting.get("document_parsing")).value, stored)
        rejected = self.client.put(self.url, headers=self.headers, json={"value": stored})
        self.assertEqual(rejected.status_code, 422)
        changed = self.client.put(self.url, headers=self.headers, json={"value": {"mode": "provider", "provider": "paddle"}})
        self.assertEqual(changed.status_code, 422)
        saved = self.client.put(self.url, headers=self.headers, json={"value": {"mode": "provider", "provider": "glm", "api_key": "new-glm-key"}})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(asyncio.run(self.stores.global_setting.get("document_parsing")).value["provider"], "glm")

    def test_switch_requires_new_provider_key_and_only_one_persists(self):
        for provider in ("paddle", "glm"):
            response = self.client.put(self.url, headers=self.headers, json={"value": {"mode": "provider", "provider": provider}})
            self.assertEqual(response.status_code, 422)
            response = self.client.put(self.url, headers=self.headers, json={"value": {"mode": "provider", "provider": provider, "api_key": provider + "-key"}})
            self.assertEqual(response.status_code, 200, response.text)
        stored = asyncio.run(self.stores.global_setting.get("document_parsing"))
        self.assertEqual(stored.value, {"mode": "provider", "provider": "glm", "api_key": "glm-key"})

    def test_admin_boundary_and_test_draft_not_saved(self):
        for method in ("GET", "PUT", "POST"):
            response = self.client.request(method, self.url + ("/test" if method == "POST" else ""),
                headers={"user": "ordinary-user", "authorization": "Bearer fake-token"}, json={"value": {"mode": "unconfigured"}} if method != "GET" else None)
            self.assertEqual(response.status_code, 403, response.text)
        result = _result("glm", connection_sample(), _glm_pages({"md_results": ["DeepAgent 123.45"]}), {})
        with patch("deep_agent.api.v1.document_parsing.parse_document", new=AsyncMock(return_value=result)):
            response = self.client.post(self.url + "/test", headers=self.headers,
                json={"value": {"mode": "provider", "provider": "glm", "api_key": "draft-key"}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        self.assertIsNone(asyncio.run(self.stores.global_setting.get("document_parsing")))
