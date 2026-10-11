"""Exercise the public tool, its immutable views and authenticated artifacts."""
from __future__ import annotations

import asyncio
import hashlib
import io
import zipfile
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pydantic_ai import RunContext, ToolReturn, ModelRetry
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_output_limits import session_file_metadata_from_history, artifact_handles_from_history
from deep_agent.db.event_codec import encode_messages
from deep_agent.documents.adaptation import adapt_document
from deep_agent.documents.cache import CachedDocument, DocumentRunCache
from deep_agent.documents.evidence import build_document_evidence, SCHEMA_VERSION
from deep_agent.documents.models import DocumentPage, ParsedDocument
from deep_agent.documents.settings import DocumentParsingRuntimeConfig
from deep_agent.documents.toolset import build_document_toolset, read_cached_document
from deep_agent.files.reading_budget import ReadingBudget
from deep_agent.files.schema import AttachmentFileRef
from deep_agent.files.storage import LocalFileStore
from deep_agent.schemas.attachment import AttachmentRef
from tests.unit.test_document_tools import pdf

RESOURCE = {"type": "attachment", "file_id": "input-pdf"}


def original(text="租金 12,500.00 元🙂"):
    return ParsedDocument("glm", "glm-ocr", [DocumentPage(1, text, native={"blocks": [
        {"label": "text", "content": text, "bbox_2d": [10, 20, 500, 60]}]}),
        DocumentPage(3, "签章处")], source_pages=4,
        native={"data_info": {"pages": [{"width": 600, "height": 800}] * 4}})


def unwrap(result):
    return result.return_value if isinstance(result, ToolReturn) else result.model_dump(mode="json")


class LayoutPaginationTests(unittest.TestCase):
    def test_layout_budget_reconstructs_fixed_text_and_same_block_across_fragments(self):
        for window in (128000, 256000):
            with self.subTest(window=window):
                source = original("中文🙂字段 | 12,500.00\n" * 8000)
                estimator = TokenEstimator.conservative()
                readable = adapt_document(source, estimator)
                record = CachedDocument("doc_test", AttachmentFileRef(**RESOURCE), "attachment:input-pdf", "a" * 64, source, readable)
                evidence = build_document_evidence(source, readable, record.document_id, estimator)
                budget = ReadingBudget(estimator, window)
                args = {"include_layout": True, "export_file": True}
                reconstructed = {1: "", 3: ""}
                block_ids, positions = set(), set()
                for _ in range(80):
                    result = read_cached_document(record, budget=budget, evidence=evidence, **args)
                    self.assertTrue(budget.fits(result.model_dump(mode="json")))
                    for page in result.pages:
                        self.assertEqual(page.char_offset, len(reconstructed[page.page_number]))
                        reconstructed[page.page_number] += page.content
                        for block in page.layout["blocks"]:
                            block_ids.add(block["block_id"])
                            self.assertLessEqual(block.get("fragment_end", block["char_end"]), page.char_end)
                            self.assertGreaterEqual(block.get("fragment_start", block["char_start"]), page.char_offset)
                    if not result.has_more:
                        break
                    args = dict(result.next_read)
                    args.pop("resource")
                    self.assertTrue(args["include_layout"])
                    self.assertTrue(args["export_file"])
                    position = (args["offset"], args["char_offset"])
                    self.assertNotIn(position, positions)
                    positions.add(position)
                else:
                    self.fail("layout did not terminate")
                self.assertEqual(list(reconstructed.values()), [p.content for p in evidence.pages])
                self.assertEqual(block_ids, {b.block_id for p in evidence.pages for b in p.blocks})
                self.assertEqual(result.status, "partial")
                self.assertEqual(result.summary.missing_pages, [2, 4])


class LayoutToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="TMP_to_delete_document_export_tool_")
        self.store = LocalFileStore(root=Path(self.directory.name))
        await self.store.initialize()
        self.ctx = RunContext(deps=AgentDeps(session_id="evidence-test", run_id="run-test", file_store=self.store,
            request_headers={"user": "owner"}, attachment_by_id={"input-pdf": AttachmentRef(
                id="input-pdf", name="contract.pdf", mime="application/pdf")}), model=TestModel(), usage=RunUsage())
        self.download = patch("deep_agent.files.toolset._download_file", AsyncMock(return_value=pdf(4)))
        self.parse = patch("deep_agent.documents.toolset.parse_with_provider", AsyncMock(return_value=original()))
        self.download.start()
        self.parser = self.parse.start()
        toolset = build_document_toolset(DocumentParsingRuntimeConfig("provider", "glm", "controlled-key"))
        self.tool = (await toolset.get_tools(self.ctx))["parse_document"]

    async def asyncTearDown(self):
        if self.ctx.deps.document_cache:
            await self.ctx.deps.document_cache.aclose()
        self.download.stop()
        self.parse.stop()
        self.directory.cleanup()

    async def call(self, **args):
        return await self.tool.call_func({"resource": RESOURCE, **args}, self.ctx)

    async def test_default_layout_only_export_only_and_both_independent_and_single_upload(self):
        with patch.object(self.store, "upload", wraps=self.store.upload) as upload:
            default = unwrap(await self.call())
            self.assertEqual(default["export"]["status"], "not_requested")
            self.assertIsNone(default["pages"][0]["layout"])
            layout = unwrap(await self.call(include_layout=True))
            self.assertIsNotNone(layout["pages"][0]["layout"])
            upload.assert_not_called()
            replies = await asyncio.gather(self.call(export_file=True, limit=1),
                                          self.call(include_layout=True, export_file=True))
            upload.assert_awaited_once()
            exported = unwrap(replies[0])
            combined = unwrap(replies[1])
            self.assertIsNone(exported["pages"][0]["layout"])
            self.assertEqual(exported["export"], combined["export"])
            ref = exported["export"]["file"]
            data = await self.store.download(ref["file_id"], self.ctx.deps.request_headers)
            self.assertEqual(hashlib.sha256(data).hexdigest(), ref["sha256"])
            self.assertEqual(len(data), ref["byte_size"])
            evidence = json.loads(zipfile.ZipFile(io.BytesIO(data)).read("evidence.json"))
            self.assertEqual(evidence["schema_version"], SCHEMA_VERSION)
            self.assertEqual(len(evidence["pages"]), 2)  # all acquired, independent of limit=1
            self.assertEqual(evidence["status"], "partial")
            self.assertEqual(evidence["source"]["storage"], "local")
            ids = {block["block_id"] for page in evidence["pages"] for block in page["blocks"]}
            for page in combined["pages"]:
                for block in page["layout"]["blocks"]:
                    self.assertIn(block["block_id"], ids)
            again = unwrap(await self.call())
            self.assertEqual(again["export"]["file"], ref)
            upload.assert_awaited_once()
            self.parser.assert_awaited_once()
            await self.ctx.deps.document_cache.aclose()
            self.assertEqual(await self.store.download(ref["file_id"], self.ctx.deps.request_headers), data)

    async def test_persisted_file_authority_survives_history_but_not_overflow_handle(self):
        result = await self.call(export_file=True)
        self.assertIsInstance(result, ToolReturn)
        part = ToolReturnPart("parse_document", result.return_value, metadata=result.metadata)
        messages = [ModelRequest(parts=[part])]
        # Actual event encoder must preserve body and application-only metadata.
        encoded = encode_messages(messages, session_id="evidence-test", run_id="run-test")
        self.assertIn("document_evidence_file", json.dumps([item.payload for item in encoded]))
        ref = result.return_value["export"]["file"]
        self.assertIn(ref["file_id"], session_file_metadata_from_history(messages))
        self.assertIn(ref["file_id"], self.ctx.deps.session_file_artifacts)
        self.assertNotIn(ref["file_id"], artifact_handles_from_history(messages))
        forged = [ModelRequest(parts=[ToolReturnPart("untrusted", result.return_value)])]
        self.assertNotIn(ref["file_id"], session_file_metadata_from_history(forged))

    async def test_export_failure_keeps_body_and_does_not_reupload_on_continuation(self):
        with patch.object(self.store, "upload", AsyncMock(side_effect=PermissionError("secret contract text"))) as upload:
            first = unwrap(await self.call(export_file=True, limit=1))
            self.assertEqual(first["export"]["status"], "failed")
            self.assertIsNone(first["export"]["file"])
            self.assertNotIn("secret", json.dumps(first))
            self.assertTrue(first["pages"][0]["content"])
            second = unwrap(await self.tool.call_func(first["next_read"], self.ctx))
            self.assertEqual(first["export"], second["export"])
            upload.assert_awaited_once()
            self.parser.assert_awaited_once()

    async def test_layout_and_export_recheck_original_authorization(self):
        first = unwrap(await self.call(include_layout=True))
        self.ctx.deps.attachment_by_id.clear()
        with self.assertRaises(ModelRetry):
            await self.tool.call_func({"resource": first["document"], "include_layout": True, "export_file": True}, self.ctx)
        self.assertEqual(self.ctx.deps.document_cache.exports.peek(first["document"]["document_id"]).status, "not_requested")

    async def test_changed_identity_does_not_receive_saved_reference_on_plain_read(self):
        saved = unwrap(await self.call(export_file=True))
        self.assertEqual(saved["export"]["status"], "saved")
        self.ctx.deps.request_headers = {"user": "different-owner"}
        value = unwrap(await self.call())
        self.assertEqual(value["export"]["status"], "failed")
        self.assertIsNone(value["export"]["file"])

    async def test_bad_layout_does_not_lose_successful_body_for_export_only(self):
        with patch("deep_agent.documents.cache.build_document_evidence", side_effect=ValueError("private native body")):
            result = unwrap(await self.call(export_file=True))
            self.assertEqual(result["export"]["status"], "failed")
            self.assertTrue(result["pages"][0]["content"])
            self.assertNotIn("private native", json.dumps(result))
            again = unwrap(await self.call())
            self.assertEqual(again["pages"], result["pages"])
            self.parser.assert_awaited_once()

    async def test_cancelling_cache_cleanup_still_joins_export_and_removes_directory(self):
        cache = DocumentRunCache()
        directory = cache.directory
        export_started, release = asyncio.Event(), asyncio.Event()
        async def export_close():
            export_started.set()
            await release.wait()
        with patch.object(cache.exports, "aclose", side_effect=export_close):
            closing = asyncio.create_task(cache.aclose())
            await export_started.wait()
            closing.cancel()
            second = asyncio.create_task(cache.aclose())
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await closing
            await second
        self.assertFalse(directory.exists())
        self.assertFalse(cache.tasks)
        self.assertFalse(cache.evidence_tasks)
