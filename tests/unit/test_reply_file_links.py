from __future__ import annotations

from base64 import b64decode
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import unittest

from starlette.datastructures import URL

from deep_agent.conversation.file_links import MarkdownFileLinks, ReplyFiles
from deep_agent.conversation.models import (
    AssistantConversationItem,
    ConversationHistoryResponse,
    ConversationOrder,
    UserConversationItem,
)
from deep_agent.files.storage import LocalFileStore
from deep_agent.local_security import LOCAL_DEV_SCOPE_KEY
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT


class ReplyFileLinksTests(unittest.IsolatedAsyncioTestCase):
    async def test_delivery_previews_use_run_scope_and_hide_unsupported_files(self):
        request = SimpleNamespace(base_url=URL("https://platform.test/site/deepfos-server/deep-agent/"))
        files = ReplyFiles(request, None, "session", {"app": "other", "space": "elsewhere"})
        source = [
            {"id": "saved", "name": "结果.csv"},
            {"id": "html", "name": "报告.html"},
            {"id": "no-extension", "name": "csv"},
        ]
        event = {"type": "final", "output": "**结果**", "output_files": source,
                 "_output_file_scope": ("owned001", "owned")}
        [projected] = [item async for item in files.event(event)]
        csv, html, no_extension = projected["output_files"]
        target = urlsplit(csv["preview_url"])
        self.assertEqual((target.netloc, target.path), ("", "/site/element-preview/"))
        params = parse_qs(target.query)
        self.assertEqual(params["id"], ["saved"])
        self.assertEqual(parse_qs(b64decode(params["sign"][0]).decode()), {"appId": ["owned001"], "space": ["owned"]})
        self.assertNotIn("preview_url", html)
        self.assertNotIn("preview_url", no_extension)
        self.assertNotIn("_output_file_scope", projected)
        self.assertEqual(projected["output"], "**结果**")
        self.assertIsNone(files.delivery(source)[0].preview_url)
        request.scope = {LOCAL_DEV_SCOPE_KEY: True}
        local = ReplyFiles(request, None, "session", {}).delivery(source)
        self.assertIn("/console/file-preview-local.html?", local[0].preview_url)

    async def test_every_stream_split_preserves_code_and_external_urls(self):
        async def url(file_id, image):
            return f"https://files.example/{file_id}" if file_id == "pic" else None

        source = (
            "正文 ![图](attachment://pic)，[无权文件](attachment://other)。\n"
            "外链 [说明](https://example.test/report.svg)\n"
            '\x60[代码](attachment://pic)\x60 和 \\[转义](attachment://pic)\n'
            '\x60\x60\x60markdown\n[块](attachment://pic)\n\x60\x60\x60\n'
            "[再下载](attachment://pic)"
        )
        expected = source.replace(
            "![图](attachment://pic)", "![图](<https://files.example/pic>)",
        ).replace("[无权文件](attachment://other)", "无权文件").replace(
            "[再下载](attachment://pic)", "[再下载](<https://files.example/pic>)",
        )
        for index in range(len(source) + 1):
            with self.subTest(index=index):
                parser = MarkdownFileLinks(url)
                actual = await parser.feed(source[:index])
                actual += await parser.feed(source[index:])
                actual += await parser.feed("", finish=True)
                self.assertEqual(actual, expected)

    async def test_linked_image_resolves_each_destination_across_stream_splits(self):
        async def url(file_id, image):
            return {"pic": "https://files.example/preview", "doc": "https://files.example/download"}.get(file_id)

        for destination, expected in [
            ("https://example.test", "[![预览](<https://files.example/preview>)](https://example.test)"),
            ("attachment://doc", "[![预览](<https://files.example/preview>)](<https://files.example/download>)"),
            ("attachment://missing", "![预览](<https://files.example/preview>)"),
        ]:
            source = f"[![预览](attachment://pic)]({destination})"
            for cut in range(len(source) + 1):
                with self.subTest(destination=destination, cut=cut):
                    parser = MarkdownFileLinks(url)
                    actual = await parser.feed(source[:cut]) + await parser.feed(source[cut:], finish=True)
                    self.assertEqual(actual, expected)

    async def test_unfinished_reference_and_normal_text(self):
        async def unexpected(*_):
            self.fail("Incomplete link cannot resolve a File ID")

        parser = MarkdownFileLinks(unexpected)
        self.assertEqual(await parser.feed("普通文字"), "普通文字")
        self.assertEqual(await parser.feed(" [文件](attach"), " ")
        self.assertEqual(await parser.feed("", finish=True), "[文件](attach")

    async def test_literal_line_stays_literal_after_an_emitted_prefix(self):
        async def resolve(file_id, _image):
            return f"https://files.example/{file_id}"

        link = "[示例](attachment://file)"
        for prefix, suffix in (("    example ", "\n\n"), ("~~~text\n" + "x" * 9000, "\n~~~\n")):
            with self.subTest(prefix_length=len(prefix)):
                parser = MarkdownFileLinks(resolve)
                self.assertEqual(await parser.feed(prefix), prefix)
                self.assertEqual(await parser.feed(link + suffix + link, finish=True),
                                 link + suffix + "[示例](<https://files.example/file>)")

    async def test_platform_route_only_uses_registered_files_and_escapes_filename(self):
        files = ReplyFiles(SimpleNamespace(base_url=URL("https://platform.test/site/deepfos-server/deep-agent/")),
                           None, "session", {"user": "owner"})
        files.add_artifact({"file_id": "trusted", "kind": "sandbox_output", "file_name": "报表 (2).txt", "mime": "text/plain"})
        files.add_artifact({"file_id": "spill", "kind": "tool_result_overflow"})
        value = await files.text("[文件](attachment://trusted) ![伪图](attachment://trusted) [未知](attachment://spill)")
        self.assertEqual(
            value,
            "[文件](<https://platform.test/site/deepfos-server/platform-file-server/files/download"
            "?id=trusted&fileName=%E6%8A%A5%E8%A1%A8+%282%29.txt>) 伪图 未知",
        )

    async def test_local_file_owner_availability_and_diagnostic_route(self):
        with TemporaryDirectory(prefix="TMP_to_delete_reply_file_store_") as directory:
            store = LocalFileStore(directory)
            await store.initialize()
            file = await store.upload(file_type=FILE_TYPE_ATTACHMENT, file_name="pic.svg", mime="image/svg+xml",
                                      data=b"<svg/>", request_headers={"user": "owner"})
            reference = {"file_id": file.id, "kind": "sandbox_output", "mime": "image/svg+xml"}
            request = SimpleNamespace(base_url=URL("http://localhost:8006/"))
            owner = ReplyFiles(request, store, "session", {"user": "owner"})
            stranger = ReplyFiles(request, store, "session", {"user": "stranger"})
            admin = ReplyFiles(request, store, "session", {"user": "owner"}, diagnostic=True)
            text = f"![图片](attachment://{file.id})"
            self.assertEqual(await owner.text(text), "图片")
            for projection in (owner, stranger, admin):
                projection.add_artifact(reference)
            self.assertEqual(await stranger.text(text), "图片")
            self.assertIn("/api/v1/local-files/", await owner.text(text))
            self.assertIn("/api/v1/diagnostics/sessions/session/files/", await admin.text(text))
            next(Path(directory, "blobs").rglob("*.blob")).unlink()
            self.assertEqual(await owner.text(text), "图片")

    async def test_history_uses_only_prior_session_files_and_preserves_original(self):
        when = datetime.now(timezone.utc)
        link = "[文件](attachment://known)"
        def fields(id, position):
            return dict(item_id=id, status="completed", order=ConversationOrder(sequence=position),
                        created_at=when, updated_at=when)
        history = ConversationHistoryResponse(session_id="s", items=[
            AssistantConversationItem(**fields("before", 1), text=link),
            UserConversationItem(**fields("input", 2), attachments=[{"id": "known", "name": "a.txt"}]),
            AssistantConversationItem(**fields("after", 3), text=link),
        ])
        projection = ReplyFiles(SimpleNamespace(base_url=URL("https://platform.test/")), None, "s", {})
        result = await projection.history(history)
        self.assertEqual(result.items[0].text, "文件")
        self.assertIn("/files/download?id=known", result.items[2].text)
        self.assertEqual(history.items[2].text, link)
