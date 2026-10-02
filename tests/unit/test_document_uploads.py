from __future__ import annotations

import io
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from deep_agent.agent.input_capabilities import (
    build_capability_instruction_block,
    document_upload_mimes,
    upload_capabilities_payload,
)
from deep_agent.agent.media_pipeline import MediaPipelineError, process_upload
from deep_agent.agent.spec import AgentSpec
from deep_agent.api.v1.uploads import router
from deep_agent.core.context import get_app_context
from deep_agent.documents.settings import DocumentParsingRuntimeConfig
from deep_agent.files.storage import StoredFile

PDF = "application/pdf"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def office_bytes(path="ppt/slides/slide1.xml", content=b"<slide>123.45</slide>", compression=zipfile.ZIP_STORED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        archive.writestr(path, content)
    return buffer.getvalue()


class DocumentUploadTests(unittest.TestCase):
    def setUp(self):
        self.options = SimpleNamespace(agent=SimpleNamespace(upload_allowed_mimes="", upload_max_size_mb=20, upload_max_count=10))
        self.patches = [patch("deep_agent.agent.media_pipeline.OPTION", self.options), patch("deep_agent.agent.input_capabilities.OPTION", self.options)]
        for target in self.patches:
            target.start()
            self.addCleanup(target.stop)

    @staticmethod
    def config(provider="mineru"):
        return DocumentParsingRuntimeConfig("provider", provider, "fake-unit-key")

    def test_active_provider_expands_defaults_without_changing_existing_uploads(self):
        payload = upload_capabilities_payload(self.config())
        self.assertEqual(payload["document_parsing"]["provider"], "mineru")
        for kind in ("pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "text_file", "image"):
            self.assertIn(kind, payload["kinds"])
        self.assertEqual(payload["blocked_document_mimes"], [])
        self.assertEqual(payload["max_size_mb"], 20)
        self.assertEqual(payload["max_count"], 10)

    def test_unconfigured_invalid_or_missing_key_does_not_expand_defaults(self):
        for config in (None, DocumentParsingRuntimeConfig("unconfigured"), DocumentParsingRuntimeConfig("provider", "mineru"), DocumentParsingRuntimeConfig("provider", "mineru", "fake", "invalid")):
            with self.subTest(config=config):
                payload = upload_capabilities_payload(config)
                self.assertNotIn("pdf", payload["kinds"])
                self.assertNotIn("pptx", payload["kinds"])
                self.assertIn("docx", payload["kinds"])
                self.assertIsNone(payload["document_parsing"])

    def test_paddle_and_glm_expand_pdf_only_preserving_direct_office_reading(self):
        for provider in ("paddle", "glm"):
            with self.subTest(provider=provider):
                payload = upload_capabilities_payload(self.config(provider))
                self.assertIn("pdf", payload["kinds"])
                self.assertNotIn("pptx", payload["kinds"])
                self.assertNotIn("doc", payload["kinds"])
                self.assertIn("docx", payload["kinds"])
                self.assertNotIn("DOCX", payload["document_parsing"]["formats"])
                with self.assertRaises(MediaPipelineError):
                    process_upload(data=office_bytes(), mime=PPTX, filename="slides.pptx", additional_mimes=document_upload_mimes(self.config(provider)))

    def test_explicit_upload_policy_remains_authoritative(self):
        self.options.agent.upload_allowed_mimes = "text/plain, application/pdf"
        payload = upload_capabilities_payload(self.config())
        self.assertEqual(set(payload["allowed_mimes"]), {"text/plain", PDF})
        self.assertIn(PPTX, payload["blocked_document_mimes"])
        self.assertNotIn(PDF, payload["blocked_document_mimes"])
        with self.assertRaisesRegex(MediaPipelineError, "上传策略"):
            process_upload(data=office_bytes(), mime=DOCX, filename="contract.docx", additional_mimes=document_upload_mimes(self.config()))

    def test_document_upload_keeps_original_bytes_and_never_extracts_text(self):
        extra = document_upload_mimes(self.config())
        for name, mime, data in (
            ("contract.pdf", PDF, b"%PDF-1.7\n123.45"),
            ("legacy.doc", "application/msword", b"legacy-office"),
            ("legacy.xls", "application/vnd.ms-excel", b"legacy-office"),
            ("legacy.ppt", "application/vnd.ms-powerpoint", b"legacy-office"),
            ("doc.docx", DOCX, office_bytes("word/document.xml")),
            ("sheet.xlsx", XLSX, office_bytes("xl/worksheets/sheet1.xml")),
            ("slides.pptx", PPTX, office_bytes()),
        ):
            with self.subTest(name=name):
                result = process_upload(data=data, mime=mime, filename=name, additional_mimes=extra)
                self.assertEqual(result.data, data)
                self.assertEqual(result.filename, name)
                self.assertIsNone(result.extracted_text)

    def test_pptx_archive_safety_is_the_existing_office_guard(self):
        extra = document_upload_mimes(self.config())
        for data in (b"not-a-zip", office_bytes("../outside.xml"), office_bytes("/root.xml"), office_bytes("C:/bad.xml")):
            with self.subTest(data=data[:12]):
                with self.assertRaises(MediaPipelineError):
                    process_upload(data=data, mime=PPTX, filename="unsafe.pptx", additional_mimes=extra)
        compressed = office_bytes(content=b"A" * 40_000, compression=zipfile.ZIP_DEFLATED)
        with self.assertRaisesRegex(MediaPipelineError, "压缩比"):
            process_upload(data=compressed, mime=PPTX, filename="bomb.pptx", additional_mimes=extra)
        with patch("deep_agent.agent.attachment_extractors.MAX_OFFICE_MEMBER_BYTES", 10):
            with self.assertRaisesRegex(MediaPipelineError, "成员过大"):
                process_upload(data=office_bytes(), mime=PPTX, filename="large.pptx", additional_mimes=extra)

    def test_global_upload_size_limit_is_not_raised_by_provider_200mb_limit(self):
        self.options.agent.upload_max_size_mb = 1
        with self.assertRaisesRegex(MediaPipelineError, "1MB limit"):
            process_upload(data=b"%PDF" + b"x" * (1024 * 1024), mime=PDF, filename="large.pdf", additional_mimes=document_upload_mimes(self.config()))

    def test_runtime_instruction_mentions_document_only_when_loaded(self):
        spec = AgentSpec(name="test", model="test", instructions="", capabilities=[])
        unavailable = build_capability_instruction_block(spec, document_config=self.config(), document_available=False)
        self.assertNotIn("parse_document", unavailable)
        enabled = build_capability_instruction_block(spec, document_config=self.config("glm"), document_available=True)
        self.assertIn("parse_document tool is enabled", enabled)
        self.assertIn("call parse_document directly instead of first trying read_file", enabled)
        self.assertIn("it does not support PDF", enabled)
        self.assertIn("GLM-OCR", enabled)
        self.assertIn("next_read", enabled)
        unconfigured = build_capability_instruction_block(spec, document_available=True)
        self.assertIn("credentials are not configured", unconfigured)

    def api_client(self, provider="mineru"):
        row = SimpleNamespace(version=1, value={"mode": "provider", "provider": provider, "api_key": "fake-unit-key"})
        self.settings = SimpleNamespace(get=AsyncMock(return_value=row))
        self.upload = AsyncMock(side_effect=lambda **kwargs: StoredFile("file-unit", kwargs["file_name"], len(kwargs["data"]), kwargs["file_type"], kwargs["mime"]))
        self.authorizer = SimpleNamespace(access_for=AsyncMock(return_value=SimpleNamespace(allowed=False)))
        ctx = SimpleNamespace(stores=SimpleNamespace(global_setting=self.settings), file_store=SimpleNamespace(upload=self.upload), console_admin_authorizer=self.authorizer)
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_app_context] = lambda: ctx
        client = TestClient(app, headers={"user": "ordinary-user", "authorization": "Bearer fake-unit-token"})
        self.addCleanup(client.close)
        return client

    def test_nonadmin_can_read_capabilities_and_upload_pdf_and_office(self):
        client = self.api_client()
        capabilities = client.get("/api/v1/uploads/capabilities")
        self.assertEqual(capabilities.status_code, 200, capabilities.text)
        self.assertEqual(capabilities.json()["document_parsing"]["provider"], "mineru")
        for filename, mime, data in (("contract.pdf", PDF, b"%PDF-1.7"), ("slides.pptx", PPTX, office_bytes())):
            result = client.post("/api/v1/uploads", files={"file": (filename, data, mime)})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["mime"], mime)
            self.assertIsNone(result.json()["extracted_text"])
        self.authorizer.access_for.assert_not_called()
        self.assertEqual(self.upload.call_count, 2)
        self.assertEqual(self.upload.call_args.kwargs["request_headers"]["user"], "ordinary-user")

    def test_api_unsupported_provider_format_never_reaches_storage(self):
        client = self.api_client("paddle")
        result = client.post("/api/v1/uploads", files={"file": ("slides.pptx", office_bytes(), PPTX)})
        self.assertEqual(result.status_code, 422, result.text)
        self.assertIn("上传策略", result.json()["detail"])
        self.upload.assert_not_called()

    def test_api_unconfigured_and_policy_and_size_are_enforced_before_storage(self):
        client = self.api_client()
        self.settings.get.return_value = None
        result = client.post("/api/v1/uploads", files={"file": ("contract.pdf", b"%PDF-1.7", PDF)})
        self.assertEqual(result.status_code, 422)
        self.settings.get.return_value = SimpleNamespace(version=1, value={"mode": "provider", "provider": "mineru", "api_key": "fake-unit-key"})
        self.options.agent.upload_allowed_mimes = "text/plain"
        result = client.post("/api/v1/uploads", files={"file": ("contract.pdf", b"%PDF-1.7", PDF)})
        self.assertEqual(result.status_code, 422)
        self.options.agent.upload_allowed_mimes = ""
        self.options.agent.upload_max_size_mb = 1
        result = client.post("/api/v1/uploads", files={"file": ("contract.pdf", b"x" * (1024 * 1024 + 1), PDF)})
        self.assertEqual(result.status_code, 422)
        self.assertIn("1MB", result.json()["detail"])
        self.upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
