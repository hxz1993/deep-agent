from __future__ import annotations

import asyncio
import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image
from pydantic_ai import Agent, ModelRetry, RunContext, ToolFailed
from pydantic_ai.messages import BinaryContent, ImageUrl, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.files.image_view.runtime import build_view_image_runtime
from deep_agent.files.image_view.toolset import _read_skill_bytes
from deep_agent.files.storage import FileStoreSizeLimitError, LocalFileStore
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.skills.runtime import ResolvedSkill


def image_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (24, 16), "blue").save(output, format="PNG")
    return output.getvalue()


def context(*refs, messages=None):
    return RunContext(
        deps=AgentDeps(session_id="view-test", attachment_by_id={r.id: r for r in refs}),
        model=TestModel(), usage=RunUsage(), messages=list(messages or []),
    )


def ref(file_id="a"):
    return {"resource": {"type": "attachment", "file_id": file_id}}


def rendered(data, pages=None):
    return {
        "format": "png", "page_count": None,
        "images": [{"page": None, "original_width": 24, "original_height": 16,
                    "width": 24, "height": 16, "media_type": "image/png",
                    "sha256": hashlib.sha256(data).hexdigest(), "data": data,
                    "scaled": False, "gif_first_frame": False}], "errors": [],
    }


async def tool(ctx, resources=None, **kwargs):
    runtime = build_view_image_runtime(resources or {}, image_capable=True,
                                      python_available=kwargs.pop("python_available", False),
                                      web_authorized=kwargs.pop("web_authorized", False), **kwargs)
    return runtime, (await runtime.toolset.get_tools(ctx)).get("view_image")


class ViewImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_filestore_bounds_second_source_by_remaining_and_missing_stays_partial(self):
        data = image_bytes()
        with tempfile.TemporaryDirectory() as directory:
            store = LocalFileStore(Path(directory))
            await store.initialize()
            headers = {"user": "owner"}
            files = [await store.upload(file_type="DL", file_name=f"{n}.png", mime="image/png", data=data,
                                        request_headers=headers) for n in range(2)]
            ctx = context(*(AttachmentRef(id=item.id, name=item.file_name, mime=item.mime) for item in files),
                          AttachmentRef(id="missing", name="missing.png"))
            ctx.deps.user, ctx.deps.file_store = "owner", store
            _, viewer = await tool(ctx)
            with patch.object(store, "download_bounded", wraps=store.download_bounded) as bounded, patch.object(
                store, "download", new=AsyncMock(side_effect=AssertionError("unbounded read")),
            ), patch("deep_agent.files.image_view.toolset.MAX_SOURCE_TOTAL_BYTES", len(data) * 2 - 1), patch(
                "deep_agent.files.image_view.toolset.render_source", new=AsyncMock(side_effect=rendered),
            ) as renderer:
                with self.assertRaises(ModelRetry):
                    await viewer.call_func({"items": [ref(item.id) for item in files]}, ctx)
                self.assertEqual([call.kwargs["max_bytes"] for call in bounded.call_args_list],
                                 [len(data) * 2 - 1, len(data) - 1])
                renderer.assert_not_awaited()
                result = await viewer.call_func({"items": [ref(files[0].id), ref("missing")]}, ctx)
            self.assertEqual(result.metadata["view_image"]["status"], "partial")
            self.assertEqual(result.metadata["view_image"]["items"][-1]["status"], "error")
            self.assertFalse(ctx.deps.image_view_state.pending)

    def test_real_skill_fd_read_is_bounded_and_rejects_links_fifos_and_replacement(self):
        from contextlib import contextmanager
        from types import SimpleNamespace
        from unittest.mock import patch
        import stat

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "a.png"
            source.write_bytes(image_bytes())
            with self.assertRaises(FileStoreSizeLimitError):
                _read_skill_bytes(root, "a.png", 4)
            # Simulate a file growing after fstat: the FD read still asks for
            # only limit+1 bytes, never for the whole changed source.
            original_stat, original_open = os.fstat, os.fdopen
            reads = []

            def small_stat(fd):
                value = original_stat(fd)
                return SimpleNamespace(st_mode=value.st_mode, st_size=1) if stat.S_ISREG(value.st_mode) else value

            @contextmanager
            def recorded_open(*args, **kwargs):
                with original_open(*args, **kwargs) as handle:
                    def read(size):
                        reads.append(size)
                        return handle.read(size)
                    yield SimpleNamespace(read=read)

            with patch("os.fstat", side_effect=small_stat), patch("os.fdopen", side_effect=recorded_open), self.assertRaises(FileStoreSizeLimitError):
                _read_skill_bytes(root, "a.png", 4)
            self.assertEqual(reads, [5])
            (root / "link.png").symlink_to(source)
            with self.assertRaises(ToolFailed):
                _read_skill_bytes(root, "link.png", 1000)
            os.mkfifo(root / "pipe.png")
            with self.assertRaises(ToolFailed):
                _read_skill_bytes(root, "pipe.png", 1000)

            @contextmanager
            def replace_after_read(*args, **kwargs):
                with original_open(*args, **kwargs) as handle:
                    yield handle
                temporary = root / "new.png"
                temporary.write_bytes(image_bytes())
                temporary.replace(source)

            with patch("os.fdopen", side_effect=replace_after_read), self.assertRaises(ToolFailed):
                _read_skill_bytes(root, "a.png", 1000)
    async def test_actual_sdk_two_visual_calls_accumulate_without_budget_double_count(self):
        ctx = context(AttachmentRef(id="a", name="a.png"))
        runtime, _ = await tool(ctx)
        counts = []

        def model(messages, info):
            returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
            if len(returns) < 2:
                return ModelResponse(parts=[ToolCallPart("view_image", {"items": [ref()]}, str(len(returns)))])
            actual = RunContext(deps=ctx.deps, model=ctx.model, usage=ctx.usage, messages=messages)
            counts.append(ctx.deps.image_view_state.context_totals(actual))
            return ModelResponse(parts=[TextPart("both visible")])

        with patch("deep_agent.files.image_view.toolset._download_file", new=AsyncMock(return_value=image_bytes())), patch(
            "deep_agent.files.image_view.toolset.render_source", new=AsyncMock(side_effect=rendered),
        ):
            result = await Agent(FunctionModel(model), deps_type=AgentDeps, toolsets=[runtime.toolset],
                                 capabilities=[runtime.lifecycle]).run("compare", deps=ctx.deps)
        self.assertEqual(result.output, "both visible")
        self.assertEqual(counts, [(2, len(image_bytes()) * 2)])
        self.assertIsNone(ctx.deps.image_view_state)
    async def test_real_pdf_pages_dedup_and_mixed_source_order(self):
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=150)
        writer.add_blank_page(width=120, height=160)
        buffer = io.BytesIO()
        writer.write(buffer)
        pdf = buffer.getvalue()
        ctx = context(AttachmentRef(id="pdf", name="pages.pdf"), AttachmentRef(id="a", name="pic.png"))
        _, viewer = await tool(ctx)

        async def download(file_id, *_args, **_kwargs):
            return pdf if file_id == "pdf" else image_bytes()

        with patch("deep_agent.files.image_view.toolset._download_file", side_effect=download):
            result = await viewer.call_func({"items": [{**ref("pdf"), "pages": [2, 2]}, ref(),
                                                       {**ref("pdf"), "pages": [1, 3]}]}, ctx)
        summary = result.metadata["view_image"]
        self.assertEqual(summary["status"], "partial")
        self.assertEqual([(item["resource"]["file_id"], item["page"]) for item in summary["items"]],
                         [("pdf", 2), ("a", None), ("pdf", 1), ("pdf", 3)])
        self.assertEqual(summary["items"][-1]["status"], "error")
        self.assertEqual(summary["items"][0]["page_count"], 2)

    async def test_source_output_and_context_byte_caps_release_reservations(self):
        data = image_bytes()
        ctx = context(AttachmentRef(id="a", name="a.png"), AttachmentRef(id="b", name="b.png"))
        _, viewer = await tool(ctx)
        with patch("deep_agent.files.image_view.toolset._download_file", new=AsyncMock(return_value=data)), patch(
            "deep_agent.files.image_view.toolset.render_source", new=AsyncMock(side_effect=rendered),
        ) as renderer:
            with patch("deep_agent.files.image_view.toolset.MAX_SOURCE_TOTAL_BYTES", len(data)), self.assertRaises(ModelRetry):
                await viewer.call_func({"items": [ref(), ref("b")]}, ctx)
            renderer.assert_not_awaited()
            with patch("deep_agent.files.image_view.toolset.MAX_CALL_OUTPUT_BYTES", len(data) - 1), self.assertRaises(ModelRetry):
                await viewer.call_func({"items": [ref()]}, ctx)
            ctx.messages = [ModelRequest(parts=[UserPromptPart(content=[BinaryContent(data=data, media_type="image/png")])])]
            with patch("deep_agent.files.image_view.runtime.MAX_CONTEXT_IMAGE_BYTES", len(data) * 2 - 1), self.assertRaises(ModelRetry):
                await viewer.call_func({"items": [ref()]}, ctx)
        self.assertFalse(ctx.deps.image_view_state.pending)
        self.assertFalse(ctx.deps.image_view_state.delivered)

    async def test_data_url_context_bytes_are_not_zero(self):
        from deep_agent.files.image_view.runtime import ImageViewState
        ctx = context(messages=[ModelRequest(parts=[UserPromptPart(content=[
            ImageUrl(url="data:image/png;base64,YWJjZA=="),
        ])])])
        self.assertEqual(ImageViewState().context_totals(ctx), (1, 4))

    async def test_next_sdk_step_uses_compressed_context_not_delivered_old_images(self):
        ctx = context(AttachmentRef(id="a", name="a.png"))
        _, viewer = await tool(ctx)
        with patch("deep_agent.files.image_view.toolset._download_file", new=AsyncMock(return_value=image_bytes())), patch(
            "deep_agent.files.image_view.toolset.render_source", new=AsyncMock(side_effect=rendered),
        ):
            await viewer.call_func({"items": [ref()]}, ctx)
        self.assertEqual(ctx.deps.image_view_state.context_totals(ctx)[0], 1)
        # A later SDK request may have compacted the completed tool result into
        # lightweight provenance. Its old native image is no longer active.
        ctx.run_step += 1
        self.assertEqual(ctx.deps.image_view_state.context_totals(ctx), (0, 0))

    async def test_repeated_cancel_reaps_render_before_returning(self):
        ctx = context(AttachmentRef(id="a", name="a.png"))
        _, viewer = await tool(ctx)
        started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def renderer(*_args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()

        with patch("deep_agent.files.image_view.toolset._download_file", new=AsyncMock(return_value=image_bytes())), patch(
            "deep_agent.files.image_view.toolset.render_source", side_effect=renderer,
        ):
            call = asyncio.create_task(viewer.call_func({"items": [ref()]}, ctx))
            await started.wait()
            call.cancel()
            await cleaning.wait()
            call.cancel()
            await asyncio.sleep(0)
            self.assertFalse(call.done())
            self.assertTrue(ctx.deps.image_view_state.tasks)
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await call
        self.assertFalse(ctx.deps.image_view_state.tasks)
        self.assertFalse(ctx.deps.image_view_state.waiters)
        self.assertFalse(ctx.deps.image_view_state.pending)
    async def test_visibility_and_url_schema_follow_real_permissions(self):
        ctx = context()
        runtime, hidden = await tool(ctx)
        self.assertIsNone(hidden)
        runtime, local = await tool(ctx, python_available=True)
        self.assertIsNotNone(local)
        self.assertNotIn('"url"', str(local.tool_def.parameters_json_schema).replace("'", '"'))
        _, public = await tool(ctx, web_authorized=True)
        self.assertIn('"url"', str(public.tool_def.parameters_json_schema).replace("'", '"'))
        self.assertIsNone(build_view_image_runtime({}, image_capable=False,
                                                  python_available=True, web_authorized=True))

    async def test_upload_and_trusted_generated_id_return_native_images_and_safe_metadata(self):
        ctx = context(AttachmentRef(id="a", name="a.png", mime="image/png"))
        ctx.deps.session_file_artifacts["generated"] = {
            "kind": "sandbox_output", "file_id": "generated", "file_name": "out.png",
            "sha256": hashlib.sha256(image_bytes()).hexdigest(),
        }
        _, viewer = await tool(ctx)
        with patch("deep_agent.files.image_view.toolset._download_file", new=AsyncMock(return_value=image_bytes())):
            result = await viewer.call_func({"items": [ref(), ref("generated")]}, ctx)
        summary = result.metadata["view_image"]
        self.assertEqual(summary["kind"], "view-image-result")
        self.assertEqual(summary["status"], "ready")
        self.assertEqual(len(summary["items"]), 2)
        self.assertEqual(len([v for v in result.return_value if isinstance(v, BinaryContent)]), 2)
        self.assertTrue(all(isinstance(item["bytes"], int) for item in summary["items"]))
        self.assertNotIn(image_bytes(), summary.values())
        self.assertEqual(ctx.deps.image_view_state.pending, {})

    async def test_unauthorized_source_and_url_do_not_download(self):
        ctx = context()
        _, viewer = await tool(ctx, python_available=True)
        with patch("deep_agent.files.image_view.toolset._download_file", new_callable=AsyncMock) as download:
            with self.assertRaises(ToolFailed):
                await viewer.call_func({"items": [ref("other-user")]}, ctx)
            with self.assertRaises(ModelRetry):
                await viewer.call_func({"items": [{"resource": {"type": "url", "url": "https://example.com/a.png"}}]}, ctx)
        download.assert_not_awaited()

    async def test_strict_pages_batch_limits_and_dedup_are_checked_before_io(self):
        ctx = context(AttachmentRef(id="a", name="a.pdf", mime="application/pdf"))
        _, viewer = await tool(ctx)
        for pages in ([True], [0], [-1], [1.5], []):
            with self.subTest(pages=pages), self.assertRaises(ModelRetry):
                await viewer.call_func({"items": [{**ref(), "pages": pages}]}, ctx)
        with self.assertRaises(ModelRetry):
            await viewer.call_func({"items": [ref(str(i)) for i in range(9)]}, ctx)
        with patch("deep_agent.files.image_view.toolset._download_file", new_callable=AsyncMock) as download:
            with self.assertRaises(ModelRetry):
                await viewer.call_func({"items": [{**ref(), "pages": list(range(1, 9))}, ref("b")]}, ctx)
        download.assert_not_awaited()

    async def test_repeat_view_uses_render_cache_but_returns_image_again(self):
        ctx = context(AttachmentRef(id="a", name="a.png", mime="image/png"))
        _, viewer = await tool(ctx)
        renderer = AsyncMock(side_effect=rendered)
        with patch("deep_agent.files.image_view.toolset._download_file", new=AsyncMock(return_value=image_bytes())) as download, patch(
            "deep_agent.files.image_view.toolset.render_source", new=renderer,
        ):
            first = await viewer.call_func({"items": [ref(), ref()]}, ctx)
            second = await viewer.call_func({"items": [ref()]}, ctx)
        self.assertEqual(len(first.metadata["view_image"]["items"]), 1)
        self.assertTrue(any(isinstance(item, BinaryContent) for item in second.return_value))
        self.assertEqual(renderer.await_count, 1)
        self.assertEqual(download.await_count, 1)

    async def test_skill_is_pinned_and_env_or_script_is_not_an_image_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SKILL.md").write_text("# Test")
            (root / "chart.png").write_bytes(image_bytes())
            (root / ".env").write_bytes(image_bytes())
            (root / "run.py").write_bytes(image_bytes())
            entries = [{"path": path, "category": category, "mime": "image/png",
                        "content_hash": hashlib.sha256(image_bytes()).hexdigest()}
                       for path, category in (("chart.png", "asset"), (".env", "asset"), ("run.py", "script"))]
            skill = ResolvedSkill("demo", "upload", root, root.parent, entries)
            ctx = context()
            _, viewer = await tool(ctx, {"demo": skill})
            request = lambda path: {"items": [{"resource": {"type": "skill", "skill_code": "demo", "path": path}}]}
            await viewer.call_func(request("chart.png"), ctx)
            for path in (".env", "run.py", "../chart.png"):
                with self.assertRaises(ToolFailed):
                    await viewer.call_func(request(path), ctx)
            (root / "SKILL.md").write_text("# Changed")
            with self.assertRaises(ToolFailed):
                await viewer.call_func(request("chart.png"), ctx)

    async def test_partial_failure_does_not_hide_the_failed_source(self):
        ctx = context(AttachmentRef(id="a", name="a.png", mime="image/png"))
        _, viewer = await tool(ctx)
        with patch("deep_agent.files.image_view.toolset._download_file", new=AsyncMock(return_value=image_bytes())):
            result = await viewer.call_func({"items": [ref(), ref("unowned")]}, ctx)
        summary = result.metadata["view_image"]
        self.assertEqual(summary["status"], "partial")
        self.assertEqual([x["status"] for x in summary["items"]], ["ready", "error"])

    async def test_context_and_parallel_reservations_enforce_joint_image_budget(self):
        current = [BinaryContent(data=b"x", media_type="image/png") for _ in range(19)]
        ctx = context(AttachmentRef(id="a", name="a.png", mime="image/png"),
                      messages=[ModelRequest(parts=[UserPromptPart(content=current)])])
        _, viewer = await tool(ctx)
        started, release = asyncio.Event(), asyncio.Event()

        async def blocked(*args):
            started.set()
            await release.wait()
            return rendered(image_bytes())

        with patch("deep_agent.files.image_view.toolset._download_file", new=AsyncMock(return_value=image_bytes())), patch(
            "deep_agent.files.image_view.toolset.render_source", side_effect=blocked,
        ):
            first = asyncio.create_task(viewer.call_func({"items": [ref()]}, ctx))
            await started.wait()
            with self.assertRaises(ModelRetry):
                await viewer.call_func({"items": [ref()]}, ctx)
            release.set()
            completed = await first
        self.assertEqual(len(completed.metadata["view_image"]["items"]), 1)
        # Once the SDK appends this result, it must not be counted twice.
        ctx.messages.append(ModelRequest(parts=[ToolReturnPart(tool_name="view_image", tool_call_id="one", content=completed.return_value)]))
        self.assertEqual(ctx.deps.image_view_state.context_totals(ctx), (20, 19 + len(image_bytes())))

    async def test_lifecycle_cleans_state_after_cancel_and_error(self):
        ctx = context(AttachmentRef(id="a", name="a.png", mime="image/png"))
        runtime, viewer = await tool(ctx)

        async def handler():
            with patch("deep_agent.files.image_view.toolset._download_file", new=AsyncMock(return_value=image_bytes())):
                await viewer.call_func({"items": [ref()]}, ctx)
            raise ValueError("failed run")

        with self.assertRaises(ValueError):
            await runtime.lifecycle.wrap_run(ctx, handler=handler)
        self.assertIsNone(ctx.deps.image_view_state)
