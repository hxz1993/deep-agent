from __future__ import annotations

import asyncio
import sys
import threading
import time
import unittest
from unittest.mock import patch

import httpx2
from pydantic_ai import ModelRetry
from pydantic_ai.messages import BinaryContent

from deep_agent.web import runtime


class WebFetchUpstreamSafetyTests(unittest.IsolatedAsyncioTestCase):
    """Exercise downloaded bytes and the real pinned converter, without public I/O."""

    async def asyncSetUp(self) -> None:
        self.body = b""
        self.content_type = "text/html"
        self.writers: set[asyncio.StreamWriter] = set()

        async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            self.writers.add(writer)
            try:
                await reader.readuntil(b"\r\n\r\n")
                headers = (
                    "HTTP/1.1 200 OK\r\n"
                    f"Content-Type: {self.content_type}\r\n"
                    f"Content-Length: {len(self.body)}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
                writer.write(headers + self.body)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                self.writers.discard(writer)

        self.server = await asyncio.start_server(serve, "127.0.0.1", 0)
        self.addAsyncCleanup(self.close_server)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/articles/page.html"
        self.fetch = runtime.web_fetch_tool(
            max_content_length=None, allow_local_urls=True, timeout=5,
        ).function
        self.enterContext(patch.dict("os.environ", {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}))

    async def close_server(self) -> None:
        self.server.close()
        await self.server.wait_closed()
        for writer in list(self.writers):
            writer.close()
            await writer.wait_closed()

    async def test_declared_and_unknown_charset_decode_real_download(self) -> None:
        for charset, body, expected in (
            ("gb18030", "中文正文".encode("gb18030"), "中文正文"),
            ("latin-1", b"caf\xe9", "café"),
            ("utf-8", b"before\xffafter", "before\ufffdafter"),
            ("unknown-codec", "正文".encode(), "正文"),
        ):
            with self.subTest(charset=charset):
                self.content_type = f"text/plain; charset={charset}"
                self.body = body
                result = await self.fetch(self.url)
                self.assertEqual(result["content"], expected)

    async def test_non_document_charset_is_correctable_failure(self) -> None:
        self.body = b"<p>Content</p>"
        for charset in ("idna", "rot_13", "base64_codec"):
            with self.subTest(charset=charset):
                self.content_type = f"text/html; charset={charset}"
                with self.assertRaisesRegex(ModelRetry, "Failed to decode"):
                    await self.fetch(self.url)

    async def test_deeply_nested_html_is_correctable_failure(self) -> None:
        self.body = ("<div>" * 2000 + "Body" + "</div>" * 2000).encode()
        with self.assertRaisesRegex(ModelRetry, "nested too deeply"):
            await self.fetch(self.url)

    async def test_real_conversion_keeps_images_and_runs_off_event_loop(self) -> None:
        self.body = (
            '<title>Chart</title><p>Before <img src="../chart.png" alt="Revenue"> after.</p>'
            '<img src="http://[broken" alt="Invalid image">'
            '<img src="javascript:alert(1)" alt="Unsafe">'
            '<script>do_not_expose()</script><style>private_css</style>'
        ).encode()
        main_thread = threading.get_ident()
        worker_threads: list[int] = []
        original_decode = runtime._decode_text
        original_convert = runtime._convert_html

        def decode(response):
            worker_threads.append(threading.get_ident())
            return original_decode(response)

        def convert(html, base_url):
            worker_threads.append(threading.get_ident())
            return original_convert(html, base_url)

        with patch.object(runtime, "_decode_text", decode), patch.object(runtime, "_convert_html", convert):
            result = await self.fetch(self.url)
        self.assertEqual(len(worker_threads), 2)
        self.assertTrue(all(thread != main_thread for thread in worker_threads))
        self.assertEqual(result["title"], "Chart")
        root_url = self.url.removesuffix("/articles/page.html")
        self.assertIn(f"Before ![Revenue]({root_url}/chart.png) after.", result["content"])
        self.assertIn("Invalid image", result["content"])
        self.assertNotIn("javascript:", result["content"])
        self.assertNotIn("do_not_expose", result["content"])
        self.assertNotIn("private_css", result["content"])

    async def test_pathological_whitespace_and_ordered_lists_stay_bounded(self) -> None:
        self.body = (
            "<p>before" + " " * 100_000 + "after</p>"
            + "<pre>\n  code" + " " * 100_000 + "end\n</pre>"
            + '<ol start="²"><li>first</li><li>second</li></ol>'
            + "<ol>" + "<li>item</li>" * 1000 + "</ol>"
        ).encode()
        started = time.monotonic()
        result = await self.fetch(self.url)
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("before after", result["content"])
        self.assertIn("1. first", result["content"])
        self.assertIn("2. second", result["content"])
        self.assertIn("1000. item", result["content"])

    async def test_repeated_unclosed_title_tags_stay_bounded(self) -> None:
        # Isolate the hostile parser case: a regression must fail with a bounded
        # timeout rather than leave a GIL-holding worker hanging the test suite.
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c",
            "from deep_agent.web.runtime import _html_to_markdown\n"
            "from pydantic_ai import ModelRetry\n"
            "try:\n"
            "    _html_to_markdown('<title' * 300000, base_url='https://example.com')\n"
            "except ModelRetry as exc:\n"
            "    assert 'incomplete HTML tags' in str(exc)\n"
            "else:\n"
            "    raise AssertionError('Expected correctable malformed HTML failure')\n",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
            self.assertEqual(process.returncode, 0, (stdout + stderr).decode())
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def test_normal_text_and_single_unclosed_tag_are_not_rewritten(self) -> None:
        for html, expected in (
            ("<p>2 < 3 and 4 > 1</p>", "2 < 3 and 4 > 1"),
            ("<p>正文</p><title unfinished", "正文"),
            ("<p>&lt;alpha &lt;beta</p>", "<alpha <beta"),
        ):
            with self.subTest(html=html):
                self.body = html.encode()
                result = await self.fetch(self.url)
                self.assertIn(expected, result["content"])

    async def test_complete_large_text_and_binary_contract_are_preserved(self) -> None:
        self.content_type = "text/plain"
        self.body = b"line\n" * 15_000 + b"TAIL_MARKER"
        result = await self.fetch(self.url)
        self.assertGreater(len(result["content"]), 50_000)
        self.assertTrue(result["content"].endswith("TAIL_MARKER"))
        for media_type, body in (("application/pdf", b"%PDF-test"), ("image/png", b"\x89PNG-test")):
            with self.subTest(media_type=media_type):
                self.content_type, self.body = media_type, body
                result = await self.fetch(self.url)
                self.assertIsInstance(result, BinaryContent)
                self.assertEqual(result.data, body)
                self.assertEqual(result.media_type, media_type)

    async def test_invalid_idna_url_and_malformed_ipv6_are_model_retries(self) -> None:
        # Rejected by the URL parser before DNS or any download, not a mocked exception.
        with self.assertRaises(ModelRetry) as caught:
            await runtime._web_fetch_tool().function("https://\uff45xample.com/")
        self.assertIsInstance(caught.exception.__cause__, httpx2.InvalidURL)
        for factory in (runtime._web_fetch_tool, runtime._web_image_view_tool):
            with self.subTest(factory=factory.__name__):
                with self.assertRaisesRegex(ModelRetry, "valid absolute HTTP"):
                    await factory().function("https://[broken/")


if __name__ == "__main__":
    unittest.main()
