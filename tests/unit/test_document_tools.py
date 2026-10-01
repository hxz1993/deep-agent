from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx2
from pydantic_ai import Agent, ModelRetry, RunContext, ToolFailed
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage
from pydantic_ai._ssrf import ResolvedUrl
from pypdf import PdfWriter

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.factory import build_agent
from deep_agent.agent.spec import AgentSpec
from deep_agent.agent.tool_output_limits import build_tool_output_limits
from deep_agent.capabilities import build_registry
from deep_agent.documents.cache import CachedDocument, DocumentRunCache
from deep_agent.documents.models import DocumentPage, DocumentParsingError, ParsedDocument
from deep_agent.documents.providers import can_resume_document, parse_document
from deep_agent.documents.runtime import build_document_runtime
from deep_agent.documents.settings import DocumentParsingRuntimeConfig
from deep_agent.documents.toolset import build_document_toolset, read_cached_document
from deep_agent.files.reading_budget import ReadingBudget
from deep_agent.files.schema import AttachmentFileRef
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.skills.runtime import ResolvedSkill

CONFIG = DocumentParsingRuntimeConfig("provider", "mineru", "fixture-key")
RESOURCE = {"type": "attachment", "file_id": "authorized-pdf"}


def pdf(count=2):
    out = io.BytesIO()
    writer = PdfWriter()
    for _ in range(count):
        writer.add_blank_page(width=100, height=100)
    writer.write(out)
    return out.getvalue()


def parsed(contents, numbers=None, source_pages=None, status="complete"):
    return ParsedDocument("mineru", "vlm", [DocumentPage(number, content) for number, content in
                         zip(numbers or range(1, len(contents) + 1), contents)],
                          source_pages if source_pages is not None else len(contents), status)


def record(contents, **kwargs):
    return CachedDocument("doc_fixture", AttachmentFileRef(type="attachment", file_id=RESOURCE["file_id"]),
                          "attachment:authorized-pdf", "hash", parsed(contents, **kwargs))


class DocumentPaginationTests(unittest.TestCase):
    def test_small_multi_page_preserves_native_body_and_blank_pages(self):
        contents = ["**原生** [链接](https://example.com)\n<table><tr><td>1.20万元</td></tr></table>", "", "代码\n```py\na = 1\n```"]
        result = read_cached_document(record(contents), budget=ReadingBudget())
        self.assertEqual([page.content for page in result.pages], contents)
        self.assertEqual([page.page_number for page in result.pages], [1, 2, 3])
        self.assertFalse(result.has_more)
        self.assertEqual(result.summary.coverage, "complete")
        self.assertIsNone(result.next_read)

    def test_budget_tiers_include_exact_envelope_and_lossless_unicode_continuation(self):
        contents = ["段落 中文🙂é\n" * 7000, "<table><tr><td>金额12,500.00</td></tr></table>" * 1500, "末页"]
        for window, ceiling in ((None, 24000), (128000, 24000), (255999, 24000), (256000, 48000), (1000000, 48000)):
            with self.subTest(window=window):
                cached = record(contents)
                budget = ReadingBudget(TokenEstimator.conservative(), window)
                args = {}
                reconstructed = {1: "", 2: "", 3: ""}
                seen = set()
                for _ in range(60):
                    result = read_cached_document(cached, budget=budget, **args)
                    value = result.model_dump(mode="json")
                    self.assertTrue(budget.fits(value))
                    self.assertLessEqual(budget.estimator.text(ToolReturnPart("read_document", result).model_response_str()), ceiling)
                    self.assertGreater(result.returned_count, 0)
                    for page in result.pages:
                        self.assertEqual(page.char_offset, len(reconstructed[page.page_number]))
                        reconstructed[page.page_number] += page.content
                        self.assertEqual(page.char_end, len(reconstructed[page.page_number]))
                    if not result.has_more:
                        break
                    args = dict(result.next_read)
                    self.assertEqual(args.pop("resource"), {"type": "document", "document_id": "doc_fixture"})
                    position = args["offset"], args["char_offset"]
                    self.assertNotIn(position, seen)
                    seen.add(position)
                else:
                    self.fail("document continuation failed to reach EOF")
                self.assertEqual(list(reconstructed.values()), contents)

    def test_page_range_limit_order_and_continuation_are_explicit(self):
        contents = [f"page{i}" for i in range(1, 9)]
        cached = record(contents)
        first = read_cached_document(cached, budget=ReadingBudget(), page_start=2, page_end=6, offset=2, limit=2)
        self.assertEqual([page.page_number for page in first.pages], [3, 4])
        self.assertEqual(first.next_read, {"resource": {"type": "document", "document_id": "doc_fixture"},
                                          "offset": 4, "char_offset": 0, "page_start": 2, "page_end": 6, "limit": 2})
        next_args = dict(first.next_read)
        next_args.pop("resource")
        last = read_cached_document(cached, budget=ReadingBudget(), **next_args)
        self.assertEqual([page.page_number for page in last.pages], [5, 6])
        self.assertFalse(last.has_more)

    def test_missing_source_pages_remain_partial_even_after_cache_eof(self):
        result = read_cached_document(record(["p1", "p3"], numbers=[1, 3], source_pages=4), budget=ReadingBudget())
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.summary.missing_pages, [2, 4])
        self.assertFalse(result.has_more)
        self.assertTrue(result.warnings)

    def test_unknown_pages_are_not_fabricated_or_selectable(self):
        cached = record(["全文，无可靠页码"], numbers=[None], source_pages=8)
        result = read_cached_document(cached, budget=ReadingBudget())
        self.assertIsNone(result.pages[0].page_number)
        self.assertEqual(result.summary.coverage, "unknown")
        self.assertEqual(result.summary.missing_pages, [])
        with self.assertRaises(ModelRetry):
            read_cached_document(cached, budget=ReadingBudget(), page_start=2)

    def test_invalid_offsets_ranges_and_character_positions_are_rejected(self):
        cached = record(["p1"])
        for args in ({"offset": 0}, {"limit": -1}, {"offset": True}, {"char_offset": -1},
                     {"char_offset": 2}, {"page_start": 4, "page_end": 3}):
            with self.subTest(args=args), self.assertRaises(ModelRetry):
                read_cached_document(cached, budget=ReadingBudget(), **args)


class DocumentToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.contexts = []
        self.download = AsyncMock(return_value=pdf())
        self.parse = AsyncMock(return_value=parsed(["第一页", "第二页"]))
        self.download_patch = patch("deep_agent.files.toolset._download_file", self.download)
        self.parse_patch = patch("deep_agent.documents.toolset.parse_document", self.parse)
        self.download_patch.start()
        self.parse_patch.start()

    async def asyncTearDown(self):
        for ctx in self.contexts:
            if ctx.deps.document_cache:
                await ctx.deps.document_cache.aclose()
        self.parse_patch.stop()
        self.download_patch.stop()

    def context(self, refs=True):
        deps = AgentDeps(session_id="document-test", run_id="run-fixture", attachment_by_id=(
            {RESOURCE["file_id"]: AttachmentRef(id=RESOURCE["file_id"], name="source.pdf", mime="application/pdf")} if refs else {}))
        ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage())
        self.contexts.append(ctx)
        return ctx

    async def tool(self, ctx, config=CONFIG, resources=None):
        return (await build_document_toolset(config, resources).get_tools(ctx))["read_document"]

    async def test_repeat_file_reference_and_document_reference_single_flight(self):
        ctx = self.context()
        tool = await self.tool(ctx)
        first, other = await asyncio.gather(tool.call_func({"resource": RESOURCE, "limit": 1}, ctx),
                                          tool.call_func({"resource": RESOURCE}, ctx))
        self.assertEqual(first.document, other.document)
        self.parse.assert_awaited_once()
        self.download.assert_awaited_once()
        next_page = await tool.call_func(first.next_read, ctx)
        self.assertEqual(next_page.pages[0].content, "第二页")
        self.assertFalse(next_page.has_more)
        beginning = await tool.call_func({"resource": RESOURCE}, ctx)
        self.assertEqual([page.content for page in beginning.pages], ["第一页", "第二页"])
        self.parse.assert_awaited_once()
        self.assertEqual(self.parse.call_args.args[1].source_pages, 2)

    async def test_source_authorization_is_rechecked_on_result_reference_and_cache_hit(self):
        ctx = self.context()
        tool = await self.tool(ctx)
        result = await tool.call_func({"resource": RESOURCE}, ctx)
        ctx.deps.attachment_by_id.clear()
        for ref in (RESOURCE, result.document.model_dump()):
            with self.assertRaises(ModelRetry):
                await tool.call_func({"resource": ref}, ctx)
        self.parse.assert_awaited_once()

    async def test_unknown_file_and_cross_run_document_are_not_authority(self):
        ctx = self.context()
        first = await (await self.tool(ctx)).call_func({"resource": RESOURCE}, ctx)
        other = self.context()
        for ref in ({"type": "attachment", "file_id": "unauthorized"}, first.document.model_dump()):
            with self.assertRaises(ModelRetry):
                await (await self.tool(other)).call_func({"resource": ref}, other)
        self.parse.assert_awaited_once()

    async def test_failed_submission_does_not_start_another_billable_task(self):
        self.parse.side_effect = DocumentParsingError("submission_unknown", "提交结果不明确")
        ctx = self.context()
        tool = await self.tool(ctx)
        for _ in range(2):
            with self.assertRaisesRegex(ToolFailed, "submission_unknown"):
                await tool.call_func({"resource": RESOURCE}, ctx)
        self.parse.assert_awaited_once()

    async def test_uploaded_task_recovers_query_single_flight_without_reposting(self):
        calls, directories = [], []
        recovered = False

        def server(request):
            calls.append((request.method, str(request.url)))
            if request.method == "POST":
                return httpx2.Response(200, json={"data": {"jobId": "original-job"}})
            if "ocr/jobs/original-job" in str(request.url):
                if not recovered:
                    return httpx2.Response(503)
                return httpx2.Response(200, json={"data": {
                    "state": "done", "resultUrl": {"jsonUrl": "https://cdn.example/result"},
                }})
            return httpx2.Response(200, content=json.dumps({"result": {"layoutParsingResults": [
                {"prunedResult": {"page_index": 0}, "markdown": {"text": "第一页原生正文"}},
                {"prunedResult": {"page_index": 1}, "markdown": {"text": "第二页原生正文"}},
            ]}}, ensure_ascii=False).encode())

        config = DocumentParsingRuntimeConfig("provider", "paddle", "fixture-key")
        ctx = self.context()
        tool = await self.tool(ctx, config)
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(server)) as client:
            async def native(config, source, directory, **kwargs):
                directories.append(directory)
                return await parse_document(config, source, directory, client=client, **kwargs)

            self.parse.side_effect = native
            with (
                patch("deep_agent.documents.providers.asyncio.sleep", new=AsyncMock()),
                patch("deep_agent.documents.providers.validate_and_resolve_url", new=AsyncMock(return_value=
                      ResolvedUrl(resolved_ip="8.8.8.8", hostname="cdn.example", port=443, is_https=True, path="/result"))),
            ):
                with self.assertRaisesRegex(ToolFailed, "service_error"):
                    await tool.call_func({"resource": RESOURCE}, ctx)
                recovered = True
                first, other = await asyncio.gather(tool.call_func({"resource": RESOURCE}, ctx),
                                                  tool.call_func({"resource": RESOURCE}, ctx))
        self.assertEqual(first.document, other.document)
        self.assertEqual(first.document.document_id, directories[0].name)
        self.assertEqual(directories, [directories[0], directories[0]])
        self.assertEqual([page.content for page in first.pages], ["第一页原生正文", "第二页原生正文"])
        self.assertEqual(len([method for method, _ in calls if method == "POST"]), 1)
        self.assertNotIn("PUT", [method for method, _ in calls])
        self.download.assert_awaited_once()

    async def test_recovery_requires_confirmed_remote_task_and_safe_error(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete_document_recovery_") as folder:
            directory = Path(folder)
            good = {"provider": "mineru", "task_id": "original-task", "uploaded": True,
                    "submission_unknown": False, "upload_unknown": False}
            for changes, code in (({}, "timeout"), ({"provider": "paddle"}, "network_error"),
                                  ({"provider": "glm"}, "timeout"), ({"uploaded": False}, "timeout"),
                                  ({"submission_unknown": True}, "timeout"), ({"upload_unknown": True}, "timeout"),
                                  ({"task_id": "../other"}, "timeout"), ({}, "submission_unknown"), ({}, "invalid_result")):
                with self.subTest(changes=changes, code=code):
                    (directory / "task.json").write_text(json.dumps(good | changes))
                    self.assertEqual(can_resume_document(directory, DocumentParsingError(code, "fixture")),
                                     not changes and code == "timeout" or changes == {"provider": "paddle"} and code == "network_error")

    async def test_unconfigured_and_unsupported_are_clear_before_network(self):
        ctx = self.context()
        with self.assertRaisesRegex(ToolFailed, "全局配置"):
            await (await self.tool(ctx, DocumentParsingRuntimeConfig("unconfigured"))).call_func({"resource": RESOURCE}, ctx)
        ctx.deps.attachment_by_id[RESOURCE["file_id"]] = AttachmentRef(id=RESOURCE["file_id"], name="script.py", mime="text/plain")
        with self.assertRaisesRegex(ModelRetry, "不支持"):
            await (await self.tool(ctx)).call_func({"resource": RESOURCE}, ctx)
        self.parse.assert_not_awaited()
        self.download.assert_not_awaited()

    async def test_corrupt_pdf_is_rejected_before_billable_parse(self):
        self.download.return_value = b"broken PDF"
        ctx = self.context()
        with self.assertRaisesRegex(ModelRetry, "PDF"):
            await (await self.tool(ctx)).call_func({"resource": RESOURCE}, ctx)
        self.parse.assert_not_awaited()

    async def test_skill_resource_hash_authorization_and_scripts(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete_document_skill_") as path:
            root = Path(path)
            data = pdf()
            (root / "asset.pdf").write_bytes(data)
            entry = {"path": "asset.pdf", "category": "asset", "mime": "application/pdf", "content_hash": hashlib.sha256(data).hexdigest()}
            skill = ResolvedSkill(code="lease", source="upload", package_root=root, library_root=root.parent, resource_index=[entry])
            ctx = self.context(False)
            tool = await self.tool(ctx, resources={"lease": skill})
            ref = {"type": "skill", "skill_code": "lease", "path": "asset.pdf"}
            result = await tool.call_func({"resource": ref}, ctx)
            self.assertEqual(result.pages[0].content, "第一页")
            entry["category"] = "script"
            with self.assertRaises(ModelRetry):
                await tool.call_func({"resource": result.document.model_dump()}, ctx)

    async def test_run_cache_closes_waiting_tasks_and_native_artifacts(self):
        cache = DocumentRunCache()
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def long_parse(directory):
            (directory / "native.json").write_text("private fixture")
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        task = asyncio.create_task(cache.get_or_parse(source_key="a", source_hash="h", provider="mineru", model="vlm",
                                                     resource=AttachmentFileRef(type="attachment", file_id="a"), parse=long_parse))
        await started.wait()
        directory = cache.directory
        await cache.aclose()
        await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(cancelled.is_set())
        self.assertFalse(directory.exists())
        self.assertEqual(cache.records, {})

    async def test_actual_sdk_receives_pages_without_second_spill_and_cleans_completed_run(self):
        text = "中文原生表格\n" * 4000
        self.parse.return_value = parsed([text], source_pages=2, status="partial")
        deps = self.context().deps
        runtime = build_document_runtime(CONFIG, {}, context_window_tokens=128000)
        spill, _ = build_tool_output_limits(request_headers={}, context_window_tokens=10000, max_output_tokens=2000, estimator=TokenEstimator.conservative())
        seen = []

        def model(messages, info):
            seen.append(messages)
            returned = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
            if not returned:
                return ModelResponse(parts=[ToolCallPart("read_document", {"resource": RESOURCE}, tool_call_id="doc-call")])
            value = returned[-1].content
            self.assertEqual(value.status, "partial")
            self.assertNotIn("overflow_handle", returned[-1].metadata or {})
            if value.has_more:
                return ModelResponse(parts=[ToolCallPart("read_document", value.next_read, tool_call_id=f"doc-{len(returned)}")])
            self.assertEqual("".join(part.content.pages[0].content for part in returned), text)
            return ModelResponse(parts=[TextPart("complete")])

        spec = AgentSpec(name="document-test", instructions="fixture", model="test", capabilities=["builtin:document"])
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(model)):
            agent = build_agent(spec, build_registry(), document_toolset=runtime.toolset,
                                document_lifecycle=runtime.lifecycle, tool_output_limits=spill)
        directory = None

        async def native(_config, source, cache_dir, **kwargs):
            nonlocal directory
            directory = cache_dir.parent
            (cache_dir / "raw.json").write_text("fixture")
            return parsed([text], source_pages=source.source_pages, status="partial")

        self.parse.side_effect = native
        output = await agent.run("read", deps=deps)
        self.assertEqual(output.output, "complete")
        self.assertGreater(len(seen), 2)
        self.assertIsNone(deps.document_cache)
        self.assertFalse(directory.exists())

    async def test_agent_opt_in_and_runtime_description_only_active_provider_formats(self):
        deps = self.context().deps
        runtime = build_document_runtime(DocumentParsingRuntimeConfig("provider", "glm", "key"), {})
        with patch("deep_agent.agent.factory._resolve_model", return_value=TestModel(call_tools=[])):
            disabled = build_agent(AgentSpec(name="document-test", instructions="fixture", model="test", capabilities=[]), build_registry(), document_toolset=runtime.toolset,
                                   document_lifecycle=runtime.lifecycle)
        observed = []

        def model(messages, info):
            observed.extend(tool.name for tool in info.function_tools)
            return ModelResponse(parts=[TextPart("ok")])

        with disabled.override(model=FunctionModel(model)):
            await disabled.run("x", deps=deps)
        self.assertNotIn("read_document", observed)
        tool = (await runtime.toolset.get_tools(self.context()))["read_document"]
        self.assertIn("GLM-OCR", tool.tool_def.description)
        self.assertIn("PDF", tool.tool_def.description)
        self.assertNotIn("DOCX", tool.tool_def.description)
