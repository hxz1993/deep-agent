"""Evidence bundles preserve native bytes and never invent original-PDF mapping."""
import asyncio
from copy import deepcopy
import hashlib
import io
import json
import threading
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

from PIL import Image

from deep_agent.documents import bundle
from deep_agent.documents.models import DocumentPage, ParsedDocument, DocumentParsingError


def image_bytes(width=100, height=150, format="PNG", orientation=None):
    stream = io.BytesIO()
    image = Image.new("RGB", (width, height), "white")
    kwargs = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[274] = orientation
        kwargs["exif"] = exif
    image.save(stream, format=format, **kwargs)
    return stream.getvalue()


def fixture(count=1, provider="paddle", url=True):
    pages = [DocumentPage(index + 1, "金额1,000.00🙂", native={
        **({"processed_image_url": f"https://images.example/{index}?secret=signed",
            "processed_image_source": "paddle_preprocessed_images" if provider == "paddle" else "glm_layout_visualization"} if url else {})
    }) for index in range(count)]
    parsed = ParsedDocument(provider, "fixture", pages, count)
    value = {"schema_version": "document-evidence-v2", "document_id": "doc_fixture", "status": "complete",
             "pages": [{"page_number": index + 1, "page_ordinal": index + 1, "width": 100, "height": 150,
                        "coordinate_space": "paddle_processed_page_pixels" if provider == "paddle" else "glm_page_pixels", "original_page_mapping": "unavailable",
                        "content": page.content, "blocks": []} for index, page in enumerate(pages)]}
    return parsed, value


def unpack(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return json.loads(archive.read("evidence.json")), {name: archive.read(name) for name in archive.namelist()}


class DocumentBundleTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_pages_exact_bytes_order_hash_and_no_url_leak(self):
        parsed, value = fixture(2)
        before = deepcopy(value)
        first, second = image_bytes(), image_bytes(format="JPEG")
        with patch.object(bundle.providers, "_request", AsyncMock(side_effect=[first, second])) as request:
            blob = await bundle.build_evidence_bundle(value, parsed, client=object())
        evidence, files = unpack(blob)
        self.assertEqual(value, before)
        self.assertEqual(list(files), ["evidence.json", "pages/page-0001.png", "pages/page-0002.jpg"])
        self.assertEqual(evidence["images"], {"status": "complete", "expected": 2, "saved": 2, "missing_page_ordinals": []})
        for page, expected in zip(evidence["pages"], [first, second]):
            info = page["image"]
            self.assertEqual(info["coordinate_mapping"], "matched")
            self.assertEqual(files[info["path"]], expected)
            self.assertEqual(info["sha256"], hashlib.sha256(expected).hexdigest())
            self.assertEqual(info["byte_size"], len(expected))
            self.assertEqual(page["original_page_mapping"], "unavailable")
        self.assertNotIn(b"secret", files["evidence.json"])
        self.assertTrue(all(call.kwargs["signed"] for call in request.await_args_list))
        self.assertTrue(all("headers" not in call.kwargs for call in request.await_args_list))

    async def test_no_urls_no_network(self):
        for provider, url in (("paddle", False), ("glm", False)):
            parsed, value = fixture(2, provider=provider, url=url)
            with patch.object(bundle.providers, "_request", AsyncMock()) as request, patch.object(bundle.httpx2, "AsyncClient") as client:
                evidence, files = unpack(await bundle.build_evidence_bundle(value, parsed))
            request.assert_not_called()
            client.assert_not_called()
            self.assertEqual(list(files), ["evidence.json"])
            self.assertEqual(evidence["images"]["status"], "unavailable")
            self.assertEqual(evidence["images"]["missing_page_ordinals"], [1, 2])
            self.assertEqual(evidence["pages"][0]["image"]["status"], "not_provided")

    async def test_partial_download_failure_keeps_json_and_success(self):
        parsed, value = fixture(3)
        with patch.object(bundle.providers, "_request", AsyncMock(side_effect=[
            DocumentParsingError("artifact_access_denied", "secreturl"), image_bytes(), RuntimeError("secreturl")])):
            evidence, files = unpack(await bundle.build_evidence_bundle(value, parsed, client=object()))
        self.assertEqual(evidence["images"], {"status": "partial", "expected": 3, "saved": 1, "missing_page_ordinals": [1, 3]})
        self.assertIn("pages/page-0002.png", files)
        self.assertEqual(evidence["status"], "complete")
        self.assertNotIn(b"secreturl", files["evidence.json"])
        self.assertEqual(evidence["pages"][0]["image"]["reason_code"], "artifact_access_denied")

    async def test_image_validation_mismatch_orientation_and_unknown_source(self):
        cases = [(image_bytes(101), "dimension_mismatch", None),
                 (image_bytes(orientation=6), "unverified", None)]
        for data, expected, source in cases:
            parsed, value = fixture()
            if source:
                parsed.pages[0].native["processed_image_source"] = source
            with patch.object(bundle.providers, "_request", AsyncMock(return_value=data)):
                evidence, _ = unpack(await bundle.build_evidence_bundle(value, parsed, client=object()))
            self.assertEqual(evidence["pages"][0]["image"]["status"], "saved")
            self.assertEqual(evidence["pages"][0]["image"]["coordinate_mapping"], expected)

    async def test_providers_have_identical_image_schema_and_honest_roles(self):
        keys = []
        for provider, role, annotated in (("paddle", "processed_page", False), ("glm", "layout_visualization", True)):
            parsed, value = fixture(2, provider=provider)
            with patch.object(bundle.providers, "_request", AsyncMock(side_effect=[image_bytes(), DocumentParsingError("artifact_access_denied", "secret")])):
                evidence, files = unpack(await bundle.build_evidence_bundle(value, parsed, client=object()))
            saved, failed = [page["image"] for page in evidence["pages"]]
            for info in (saved, failed):
                self.assertEqual(info["role"], role)
                self.assertEqual(info["annotated"], annotated)
            self.assertEqual(saved["coordinate_mapping"], "matched")
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(evidence["pages"][0]["original_page_mapping"], "unavailable")
            self.assertEqual(evidence["images"]["status"], "partial")
            self.assertEqual(len(files), 2)
            keys.append((set(saved), set(failed)))
        self.assertEqual(keys[0], keys[1])

    async def test_unknown_or_cross_provider_image_source_not_downloaded(self):
        for provider, source in (("paddle", "unknown"), ("glm", "paddle_preprocessed_images"), ("paddle", "glm_layout_visualization")):
            parsed, value = fixture(provider=provider)
            parsed.pages[0].native["processed_image_source"] = source
            with patch.object(bundle.providers, "_request", AsyncMock()) as request, patch.object(bundle.httpx2, "AsyncClient") as client:
                evidence, files = unpack(await bundle.build_evidence_bundle(value, parsed))
            request.assert_not_called()
            client.assert_not_called()
            self.assertEqual(evidence["pages"][0]["image"]["reason_code"], "unsupported_image_source")
            self.assertEqual(list(files), ["evidence.json"])

    async def test_glm_visualization_must_match_its_coordinate_plane(self):
        for data, coordinate_space, expected in ((image_bytes(101), "glm_page_pixels", "dimension_mismatch"),
                                                 (image_bytes(), "unknown", "unverified"),
                                                 (image_bytes(orientation=6), "glm_page_pixels", "unverified")):
            parsed, value = fixture(provider="glm")
            value["pages"][0]["coordinate_space"] = coordinate_space
            with patch.object(bundle.providers, "_request", AsyncMock(return_value=data)):
                evidence, _ = unpack(await bundle.build_evidence_bundle(value, parsed, client=object()))
            info = evidence["pages"][0]["image"]
            self.assertEqual(info["coordinate_mapping"], expected)
            self.assertEqual(info["role"], "layout_visualization")
            self.assertTrue(info["annotated"])

    async def test_malicious_or_unsupported_images_not_stored(self):
        for data in (b'<svg onload="alert(1)"/>', image_bytes(format="GIF"), image_bytes()[:60]):
            parsed, value = fixture()
            with patch.object(bundle.providers, "_request", AsyncMock(return_value=data)):
                evidence, files = unpack(await bundle.build_evidence_bundle(value, parsed, client=object()))
            self.assertEqual(list(files), ["evidence.json"])
            self.assertEqual(evidence["pages"][0]["image"]["status"], "failed")
        parsed, value = fixture()
        with patch.object(bundle.providers, "_request", AsyncMock(return_value=image_bytes())), patch.object(bundle, "MAX_IMAGE_PIXELS", 10):
            evidence, _ = unpack(await bundle.build_evidence_bundle(value, parsed, client=object()))
        self.assertEqual(evidence["pages"][0]["image"]["reason_code"], "image_pixels_exceeded")

    async def test_download_budget_and_single_image_limit(self):
        parsed, value = fixture(2)
        with patch.object(bundle, "MAX_IMAGE_BYTES", 10), patch.object(bundle.providers, "_request", AsyncMock(return_value=b"x" * 11)) as request:
            evidence, _ = unpack(await bundle.build_evidence_bundle(value, parsed, client=object()))
        self.assertEqual(request.await_count, 2)
        self.assertEqual(evidence["images"]["saved"], 0)
        self.assertEqual(evidence["pages"][0]["image"]["reason_code"], "result_too_large")
        with patch.object(bundle, "max_upload_bytes", return_value=4096), patch.object(bundle.providers, "_request", AsyncMock()) as request:
            evidence, _ = unpack(await bundle.build_evidence_bundle(value, parsed, client=object()))
        request.assert_not_called()
        self.assertEqual(evidence["pages"][0]["image"]["reason_code"], "download_budget_exceeded")

    async def test_json_size_rejected_before_network_and_unicode_exact(self):
        parsed, value = fixture()
        value["pages"][0]["content"] = '🙂中文\\"\n' * 100_000
        with patch.object(bundle, "max_upload_bytes", return_value=10_000), patch.object(bundle.providers, "_request", AsyncMock()) as request:
            with self.assertRaises(DocumentParsingError) as error:
                await bundle.build_evidence_bundle(value, parsed, client=object())
        self.assertEqual(error.exception.code, "bundle_too_large")
        request.assert_not_called()
        value["pages"][0]["content"] = '🙂中文\\"\n' * 5000
        parsed.pages[0].native.clear()
        evidence, _ = unpack(await bundle.build_evidence_bundle(value, parsed))
        self.assertEqual(evidence["pages"][0]["content"], value["pages"][0]["content"])

    async def test_aggregate_uncompressed_and_zip_limits(self):
        with self.assertRaises(DocumentParsingError):
            bundle._zip({"text": "a" * 900}, [("pages/page-0001.png", b"a" * 200)], 1024)
        with self.assertRaises(DocumentParsingError):
            bundle._zip({}, [("pages/page-0001.png", b"a" * 90)], 100)

    async def test_timeout_marks_remaining_pages(self):
        parsed, value = fixture(2)
        async def slow(*args, **kwargs):
            await asyncio.sleep(1)
        with patch.object(bundle, "IMAGE_TIMEOUT_SECONDS", 0.01), patch.object(bundle.providers, "_request", slow):
            evidence, _ = unpack(await bundle.build_evidence_bundle(value, parsed, client=object()))
        self.assertTrue(all(page["image"]["reason_code"] == "download_timeout" for page in evidence["pages"]))

    async def test_cancel_download_propagates_and_no_zip(self):
        parsed, value = fixture()
        started = asyncio.Event()
        async def waiting(*args, **kwargs):
            started.set()
            await asyncio.Future()
        with patch.object(bundle.providers, "_request", waiting), patch.object(bundle, "_zip") as zipped:
            task = asyncio.create_task(bundle.build_evidence_bundle(value, parsed, client=object()))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        zipped.assert_not_called()

    async def test_cancel_joins_image_worker(self):
        parsed, value = fixture()
        started, release, done = threading.Event(), threading.Event(), threading.Event()
        def inspect(data):
            started.set()
            release.wait(5)
            done.set()
            return ("png", "image/png", 100, 150, True)
        with patch.object(bundle.providers, "_request", AsyncMock(return_value=image_bytes())), patch.object(bundle, "_inspect_image", inspect):
            task = asyncio.create_task(bundle.build_evidence_bundle(value, parsed, client=object()))
            try:
                while not started.is_set():
                    await asyncio.sleep(.001)
                task.cancel()
                await asyncio.sleep(.01)
                self.assertFalse(task.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(done.is_set())

    async def test_signed_download_rejects_local_urls_before_http(self):
        parsed, value = fixture()
        for url in ("file:///etc/passwd", "http://127.0.0.1/private", "https://127.0.0.1/private"):
            parsed.pages[0].native["processed_image_url"] = url
            with patch.object(bundle.providers, "_stream") as stream:
                evidence, files = unpack(await bundle.build_evidence_bundle(value, parsed, client=object()))
            stream.assert_not_called()
            self.assertEqual(evidence["pages"][0]["image"]["reason_code"], "unsafe_url")
            self.assertEqual(list(files), ["evidence.json"])

    async def test_unknown_page_number_uses_ordinal_paths(self):
        parsed, value = fixture(2)
        value["pages"][0]["page_number"] = None
        value["pages"][1]["page_number"] = 1
        with patch.object(bundle.providers, "_request", AsyncMock(return_value=image_bytes())):
            evidence, files = unpack(await bundle.build_evidence_bundle(value, parsed, client=object()))
        self.assertEqual(evidence["pages"][0]["page_number"], None)
        self.assertEqual(evidence["pages"][1]["page_number"], 1)
        self.assertEqual(list(files), ["evidence.json", "pages/page-0001.png", "pages/page-0002.png"])

    async def test_cancel_joins_zip_worker(self):
        parsed, value = fixture(url=False)
        started, release, done = threading.Event(), threading.Event(), threading.Event()
        def zip_work(*args):
            started.set()
            release.wait(5)
            done.set()
            return b"zip"
        with patch.object(bundle, "_zip", zip_work):
            task = asyncio.create_task(bundle.build_evidence_bundle(value, parsed))
            try:
                while not started.is_set():
                    await asyncio.sleep(.001)
                task.cancel()
                await asyncio.sleep(.01)
                self.assertFalse(task.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(done.is_set())

    async def test_bundle_page_limit_and_safe_stage_logs(self):
        parsed, value = fixture(2)
        with patch.object(bundle, "MAX_BUNDLE_PAGES", 1), patch.object(bundle.providers, "_request", AsyncMock()) as request:
            with self.assertRaises(DocumentParsingError) as error:
                await bundle.build_evidence_bundle(value, parsed, client=object())
        self.assertEqual(error.exception.code, "bundle_page_limit")
        request.assert_not_called()
        with patch.object(bundle.providers, "_request", AsyncMock(return_value=image_bytes())), patch.object(bundle.providers, "_log") as log:
            await bundle.build_evidence_bundle(value, parsed, context={"run_id": "fixture-run"}, client=object())
        calls = log.call_args_list
        self.assertEqual([call.args[1] for call in calls], ["document_image_download", "document_image_download", "document_bundle_completed"])
        self.assertEqual(calls[-1].kwargs["saved_images"], 2)
        self.assertEqual(calls[-1].kwargs["missing_images"], 0)
        self.assertEqual(calls[-1].kwargs["status"], "saved")
        self.assertNotIn("secret", repr(calls))
        self.assertNotIn("金额", repr(calls))

    async def test_page_count_disagreement_refused(self):
        parsed, value = fixture()
        value["pages"] = []
        with self.assertRaises(DocumentParsingError) as error:
            await bundle.build_evidence_bundle(value, parsed)
        self.assertEqual(error.exception.code, "bundle_page_mismatch")


if __name__ == "__main__":
    unittest.main()
