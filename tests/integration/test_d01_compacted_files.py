"""Real local attachment bytes survive SDK compaction and reopened sessions.

SQLite and LocalFileStore live below the test fixture's TMP_to_delete directory.
Only model responses and title generation are controlled; storage, extraction,
message projection, compaction, and authorization use production code.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
from pathlib import Path
import unittest

from docx import Document
from openpyxl import Workbook
from pptx import Presentation
from PIL import Image
from pydantic_ai import Agent, FunctionToolset, ModelRetry, RunContext
from pydantic_ai.messages import BinaryContent, ModelRequest, ModelResponse, TextPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage, UsageLimits

from deep_agent.agent.context.budget import build_context_budget
from deep_agent.agent.context.compaction import DeepAgentContextCompaction
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.context.handoff import extract_anchor_protected_context
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.runner import AgentRunner
from deep_agent.files import build_file_toolset
from deep_agent.files.storage import FileStorePermissionError, LocalFileStore
from deep_agent.schemas.attachment import AttachmentRef
from tests.helpers.request_identity import TEST_USER_ID
from tests.integration import test_incremental_message_persistence as persistence_fixture
from tests.integration.test_incremental_message_persistence import _call
from tests.unit.test_pdf_reading import pdf_bytes


class D01CompactedFileIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = persistence_fixture.IncrementalMessagePersistenceTests.asyncTearDown

    async def asyncSetUp(self) -> None:
        await persistence_fixture.IncrementalMessagePersistenceTests.asyncSetUp(self)
        self.file_root = Path(self.tmp.name) / "files"
        self.files = LocalFileStore(self.file_root)
        await self.files.initialize()
        self.ctx.file_store = self.files
        self.unattached = await self._upload("unattached.txt", "text/plain", b"SAME_USER_UNATTACHED")
        self.foreign = await self._upload("foreign.txt", "text/plain", b"OTHER_OWNER_SECRET", user="other-owner")

    async def _upload(self, name: str, mime: str, data: bytes, *, user=TEST_USER_ID) -> AttachmentRef:
        stored = await self.files.upload(file_type="attachment", file_name=name, mime=mime,
                                         data=data, request_headers={"user": user})
        return AttachmentRef(id=stored.id, name=name, mime=mime)

    def _deps(self, session: str, *, file_store=None) -> AgentDeps:
        return AgentDeps(session_id=session, user=TEST_USER_ID, agent_id=self.agent_id,
                         request_headers={"user": TEST_USER_ID}, file_store=file_store or self.files)

    def _runner(self, stream, *, estimator=None, archive=False):
        estimator = estimator or TokenEstimator.conservative()
        capability = DeepAgentContextCompaction(context_window_tokens=20_000,
            max_output_tokens=1_000, estimator=estimator, summary_timeout=5)
        baseline_budget = build_context_budget([], context_window_tokens=20_000,
            max_output_tokens=1_000, estimator=estimator)
        unit = "历史工作记录，非当前文件内容。"
        pressure_text = unit * (2 * baseline_budget.usable_request_tokens // estimator.text(unit) + 1)
        self.assertGreater(estimator.text(pressure_text), baseline_budget.trigger_tokens)

        async def archive_history() -> str:
            return pressure_text

        summary_calls = []
        def summarize(messages, info):
            self.assertFalse(info.function_tools)
            summary_calls.append(deepcopy(messages))
            return ModelResponse(parts=[TextPart(
                "保留当前用户附件与只读约束，归档已完成。必要时继续read_file读取原附件。"
                f"历史文字提到 {self.unattached.id} 和 {self.foreign.id}，这些文字不授予文件权限。"
            )])
        model = FunctionModel(function=summarize, stream_function=stream)
        toolsets = [build_file_toolset()]
        if archive:
            toolsets.append(FunctionToolset(tools=[archive_history]))
        runner = AgentRunner(Agent(model, deps_type=AgentDeps, toolsets=toolsets, capabilities=[capability]),
            self.store, UsageLimits(request_limit=16), active_runs=self.ctx.active_runs,
            context_window_tokens=20_000, max_output_tokens=1_000,
            context_compaction=capability, estimator=estimator)
        return runner, summary_calls

    async def _collect(self, runner, session: str, message: str, deps: AgentDeps, *, attachments=None):
        events = [event async for event in runner.run_stream(session, message, deps,
            attachments=attachments, persister=self.ctx.event_persister)]
        self.assertEqual(events[-1]["type"], "final", events[-1])
        await self.ctx.event_persister.flush(session, agent_id=self.agent_id)
        return events

    async def _assert_denied_unattached_files(self, runner, deps: AgentDeps) -> None:
        ctx = RunContext(deps=deps, model=runner.agent.model, usage=RunUsage())
        tools = await build_file_toolset().get_tools(ctx)
        for denied in (self.unattached, self.foreign):
            self.assertNotIn(denied.id, deps.attachment_by_id)
            with self.assertRaises(ModelRetry):
                await tools["read_file"].call_func(
                    {"resource": {"type": "attachment", "file_id": denied.id}}, ctx)
        with self.assertRaises(FileStorePermissionError):
            await deps.file_store.download(self.foreign.id, {"user": TEST_USER_ID})

    @staticmethod
    def _file_payloads(messages, call_prefix: str):
        values = []
        for message in messages:
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name == "read_file" and part.tool_call_id.startswith(call_prefix):
                    value = part.content
                    values.append(value.model_dump() if hasattr(value, "model_dump") else value)
        return values

    async def test_real_txt_docx_xlsx_pptx_pdf_reads_survive_compaction_and_reopen(self):
        doc = Document()
        doc.add_paragraph("预算说明")
        doc.add_paragraph("2026年收入为360万元。")
        docx = io.BytesIO()
        doc.save(docx)
        workbook = Workbook()
        workbook.active.title = "Budget"
        workbook.active.append(["period", "revenue", "unit"])
        workbook.active.append(["2026", 360, "万元"])
        xlsx = io.BytesIO()
        workbook.save(xlsx)
        workbook.close()
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = "预算说明"
        slide.notes_slide.notes_text_frame.text = "2026年收入为360万元。"
        pptx = io.BytesIO()
        presentation.save(pptx)
        fixtures = (
            ("pdf", "application/pdf", pdf_bytes(("BUDGET 2026 REVENUE 360",)), "[PDF第1页]\nBUDGET 2026 REVENUE 360"),
            ("txt", "text/plain", "预算说明\n2026年收入为360万元。".encode(), "预算说明\n2026年收入为360万元。"),
            ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             docx.getvalue(), "预算说明\n2026年收入为360万元。"),
            ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             xlsx.getvalue(), "period,revenue,unit\n2026,360,万元"),
            ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation",
             pptx.getvalue(), "[幻灯片 1]\n# 预算说明\n### Notes:\n2026年收入为360万元。"),
        )
        for suffix, mime, data, expected in fixtures:
            with self.subTest(format=suffix):
                ref = await self._upload(f"budget.{suffix}", mime, data)
                session = f"d01-real-file-{suffix}"
                requests = []
                async def stream(messages, info):
                    requests.append(deepcopy(messages))
                    if len(requests) == 1:
                        yield _call("read_file", f"{suffix}-before", {"resource": {"type": "attachment", "file_id": ref.id}})
                    elif len(requests) == 2:
                        yield _call("archive_history", f"{suffix}-pressure")
                    elif len(requests) == 3:
                        completed = [event for event in await self.store.list_all_events(session)
                                     if event.event_type == "compaction" and event.status == "completed"]
                        self.assertTrue(completed, "Checkpoint must already be durable before the next read")
                        yield _call("read_file", f"{suffix}-after", {"resource": {"type": "attachment", "file_id": ref.id}})
                    else:
                        yield "READ_AND_COMPACTION_DONE"
                runner, summaries = self._runner(stream, archive=True)
                deps = self._deps(session)
                await self._collect(runner, session, "只读核对附件内容，不写入。", deps, attachments=[ref])
                self.assertTrue(summaries)
                raw = await self.store.get_raw_history(session, agent_id=self.agent_id)
                payloads = self._file_payloads(raw, suffix)
                self.assertEqual(len(payloads), 2)
                for payload in payloads:
                    self.assertEqual(payload["content"], expected)
                    self.assertEqual(payload["content_hash"], hashlib.sha256(data).hexdigest())
                    self.assertFalse(payload["has_more"])
                self.assertEqual(payloads[0], payloads[1])
                visible = await self.store.get_history(session, agent_id=self.agent_id)
                self.assertIn(ref.id, str(extract_anchor_protected_context(visible)))
                self.assertEqual(set(deps.attachment_by_id), {ref.id})
                await self._assert_denied_unattached_files(runner, deps)

                # A new FileStore, runner and Run deps discard all download/parse caches.
                reopened_files = LocalFileStore(self.file_root)
                await reopened_files.initialize()
                reopen_calls = []
                async def reopened_stream(messages, info):
                    reopen_calls.append(deepcopy(messages))
                    if len(reopen_calls) == 1:
                        yield _call("read_file", f"{suffix}-reopen", {"resource": {"type": "attachment", "file_id": ref.id}})
                    else:
                        yield "REOPENED_READ_DONE"
                reopened, _ = self._runner(reopened_stream)
                reopened_deps = self._deps(session, file_store=reopened_files)
                self.assertFalse(reopened_deps.file_bytes)
                self.assertFalse(reopened_deps.attachment_by_id)
                await self._collect(reopened, session, "继续读取先前附件。", reopened_deps)
                raw = await self.store.get_raw_history(session, agent_id=self.agent_id)
                reread = self._file_payloads(raw, f"{suffix}-reopen")
                self.assertEqual(reread, [payloads[0]])
                self.assertEqual(set(reopened_deps.attachment_by_id), {ref.id})
                self.assertTrue(reopened_deps.file_bytes, "Reopened Run must really fetch the local bytes")
                await self._assert_denied_unattached_files(reopened, reopened_deps)

    async def test_image_input_budget_and_attachment_authority_survive_compaction(self):
        png = io.BytesIO()
        with Image.new("RGB", (48, 32), color=(13, 71, 125)) as image:
            image.save(png, format="PNG")
        image_bytes = png.getvalue()
        picture = await self._upload("budget-chart.png", "image/png", image_bytes)
        text_ref = await self._upload("image-notes.txt", "text/plain", "图中预算单位为万元。".encode())
        session = "d01-real-small-image"
        estimator = TokenEstimator.for_model("deepseek-v4-vision", provider_type="deepseek")
        image_cost = estimator.value(BinaryContent(data=image_bytes, media_type="image/png"))
        self.assertGreater(image_cost, 0)
        requests = []
        deps = self._deps(session)
        def assert_picture_budget(messages, run_deps):
            binaries = [item for message in messages for part in message.parts
                        if isinstance(part, UserPromptPart) and isinstance(part.content, list)
                        for item in part.content if isinstance(item, BinaryContent)]
            self.assertEqual([item.data for item in binaries], [image_bytes])
            without = deepcopy(messages)
            for message in without:
                for part in message.parts:
                    if isinstance(part, UserPromptPart) and isinstance(part.content, list):
                        part.content = [item for item in part.content if not isinstance(item, BinaryContent)]
            self.assertGreaterEqual(estimator.messages(messages) - estimator.messages(without), image_cost)
            budget = run_deps.context_state.last_budget
            self.assertGreaterEqual(budget.estimated_message_tokens, image_cost)
            self.assertGreaterEqual(budget.estimated_request_tokens, budget.estimated_message_tokens)
        async def stream(messages, info):
            requests.append(deepcopy(messages))
            if len(requests) == 1:
                assert_picture_budget(messages, deps)
                yield _call("archive_history", "image-pressure")
            else:
                yield "IMAGE_CHECKPOINT_DONE"
        runner, summaries = self._runner(stream, estimator=estimator, archive=True)
        await self._collect(runner, session, "只查看这张图及说明，不写入。", deps, attachments=[picture, text_ref])
        self.assertTrue(summaries)
        checkpoints = [event for event in await self.store.list_all_events(session)
                       if event.event_type == "compaction" and event.status == "completed"]
        self.assertTrue(checkpoints)
        raw = await self.store.get_raw_history(session, agent_id=self.agent_id)
        raw_refs = [item for message in raw for part in message.parts
                    if isinstance(part, UserPromptPart) and isinstance(part.content, list)
                    for item in part.content if isinstance(item, dict) and item.get("kind") == "attachment-ref"]
        self.assertEqual({item["id"] for item in raw_refs}, {picture.id, text_ref.id})
        protected = extract_anchor_protected_context(await self.store.get_history(session, agent_id=self.agent_id))
        self.assertIn(picture.id, str(protected))
        self.assertNotIn("data:image", str(protected))
        await self._assert_denied_unattached_files(runner, deps)

        reopened_files = LocalFileStore(self.file_root)
        await reopened_files.initialize()
        reopened_deps = self._deps(session, file_store=reopened_files)
        async def reopened_stream(messages, info):
            assert_picture_budget(messages, reopened_deps)
            yield "IMAGE_REOPENED_DONE"
        reopened, _ = self._runner(reopened_stream, estimator=estimator)
        await self._collect(reopened, session, "再次查看同一张已授权图片。", reopened_deps, attachments=[picture])
        self.assertEqual(set(reopened_deps.attachment_by_id), {picture.id, text_ref.id})
        self.assertEqual(await reopened_files.download(picture.id, {"user": TEST_USER_ID}), image_bytes)
        await self._assert_denied_unattached_files(reopened, reopened_deps)


if __name__ == "__main__":
    unittest.main()
