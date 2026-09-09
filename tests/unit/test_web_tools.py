from __future__ import annotations

import asyncio
import ssl
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx2
from pydantic_ai import ModelRetry, Tool, ToolFailed
from pydantic_ai.messages import ImageUrl, ModelResponse, NativeToolReturnPart, ToolReturn
from pydantic_ai.capabilities import WebSearch
from pydantic_ai.toolsets import CombinedToolset, FunctionToolset

from deep_agent.ptc.file_payload import CODE_MODE_METADATA_KEY
from deep_agent.web.package import build_web_tool_package
from deep_agent.web.runtime import (
    _search_duckduckgo,
    _search_tavily,
    _html_to_markdown,
    _web_fetch_tool,
    _web_image_view_tool,
    _native_result_sources,
    build_web_runtime,
    execute_provider_search,
)
from deep_agent.web.settings import WebSearchRuntimeConfig, load_web_search_config


class WebPackageTests(unittest.TestCase):
    def test_builtin_package_exposes_search_fetch_and_image_view(self) -> None:
        package = build_web_tool_package()

        self.assertEqual(package.code, "builtin:web")
        self.assertEqual(package.label, "Web")
        self.assertEqual(
            [tool.name for tool in package.tools],
            ["web_search", "web_fetch", "web_image_view"],
        )
        self.assertEqual(
            [tool.display_name for tool in package.tools],
            ["网络搜索", "获取网页内容", "查看网络图片"],
        )

    def test_native_mode_keeps_fetch_local_and_search_native(self) -> None:
        runtime = build_web_runtime(WebSearchRuntimeConfig(mode="native"))

        self.assertIsInstance(runtime.toolset, FunctionToolset)
        self.assertEqual(set(runtime.toolset.tools), {"web_fetch"})
        self.assertIsInstance(runtime.capability, WebSearch)
        fetch = runtime.toolset.tools["web_fetch"]
        self.assertNotIn(CODE_MODE_METADATA_KEY, fetch.metadata)
        self.assertTrue(fetch.metadata["web_content_untrusted"])

    def test_visual_model_gets_outer_image_tool_without_code_mode_metadata(self) -> None:
        runtime = build_web_runtime(
            WebSearchRuntimeConfig(mode="native"),
            image_capable=True,
        )

        self.assertEqual(set(runtime.toolset.tools), {"web_fetch", "web_image_view"})
        image_tool = runtime.toolset.tools["web_image_view"]
        self.assertNotIn(CODE_MODE_METADATA_KEY, image_tool.metadata)
        self.assertTrue(image_tool.metadata["web_content_untrusted"])

    def test_provider_mode_exposes_stable_search_and_fetch_tools(self) -> None:
        runtime = build_web_runtime(
            WebSearchRuntimeConfig(mode="provider", provider="duckduckgo")
        )

        self.assertIsInstance(runtime.toolset, CombinedToolset)
        self.assertIsNone(runtime.capability)
        names = {
            name
            for toolset in runtime.toolset.toolsets
            for name in toolset.tools
        }
        self.assertEqual(names, {"web_search", "web_fetch"})
        search = runtime.toolset.toolsets[0].tools["web_search"]
        self.assertNotIn(CODE_MODE_METADATA_KEY, search.metadata)


class WebSearchCredentialTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_resolves_reused_llm_provider_without_storing_key(self) -> None:
        class Store:
            async def get(self, key: str):
                self.key = key
                return SimpleNamespace(
                    version=1,
                    value={
                        "mode": "provider",
                        "provider": "deepseek",
                        "model": "deepseek-v4-flash",
                        "credential_source": "llm_provider",
                        "llm_provider_id": "provider-1",
                    },
                )

        class LlmConfiguration:
            async def get_provider(self, provider_id: str):
                self.provider_id = provider_id
                return SimpleNamespace(
                    id=provider_id,
                    name="DeepSeek Session",
                    provider_type="deepseek",
                    api_key="session-secret",
                )

        config = await load_web_search_config(Store(), LlmConfiguration())

        self.assertEqual(config.api_key, "session-secret")
        self.assertEqual(config.llm_provider_name, "DeepSeek Session")
        self.assertEqual(config.to_stored_value()["llm_provider_id"], "provider-1")
        self.assertNotIn("api_key", config.to_stored_value())


class WebFetchSafetyTests(unittest.IsolatedAsyncioTestCase):
    def test_html_markdown_preserves_safe_images_in_place(self) -> None:
        markdown = _html_to_markdown(
            """
            <main>
              <p>Before <img src="../images/chart.png" alt="Revenue"> after.</p>
              <img src="//cdn.example.com/photo.webp" alt="Photo">
              <img src="javascript:alert(1)" alt="Unsafe">
              <script>window.secret = 'do not expose';</script>
              <style>.private { color: red; }</style>
            </main>
            """,
            base_url="https://example.com/articles/2026/page.html",
        )

        self.assertIn(
            "Before ![Revenue](https://example.com/articles/images/chart.png) after.",
            markdown,
        )
        self.assertIn("![Photo](https://cdn.example.com/photo.webp)", markdown)
        self.assertIn("Unsafe", markdown)
        self.assertNotIn("javascript:", markdown)
        self.assertNotIn("window.secret", markdown)
        self.assertNotIn("color: red", markdown)

    async def test_fetch_adapter_preserves_text_json_pdf_and_image_contracts(self) -> None:
        cases = (
            ("text/plain", b"plain body", "plain body", None),
            ("application/json", b'{"ok": true}', '"ok": true', None),
            ("application/pdf", b"%PDF-demo", None, "application/pdf"),
            ("image/png", b"\x89PNG-demo", None, "image/png"),
        )
        for media_type, body, expected_text, expected_binary in cases:
            with self.subTest(media_type=media_type):
                response = httpx2.Response(
                    200,
                    headers={"content-type": media_type},
                    content=body,
                    request=httpx2.Request("GET", "https://example.com/resource"),
                )
                with patch(
                    "deep_agent.web.runtime.safe_download",
                    new=AsyncMock(return_value=response),
                ):
                    result = await _web_fetch_tool().function(
                        "https://example.com/resource"
                    )
                value = result.return_value
                if expected_binary:
                    self.assertEqual(value.media_type, expected_binary)
                    self.assertEqual(value.data, body)
                else:
                    self.assertIn(expected_text, value["content"])

    async def test_blocks_non_http_private_metadata_and_credentials(self) -> None:
        fetch = _web_fetch_tool().function
        cases = (
            ("ftp://example.com/file", r"HTTP\(S\)"),
            ("http://127.0.0.1/", "private/internal"),
            ("http://169.254.169.254/latest/meta-data/", "cloud metadata"),
            ("https://user:password@example.com/", "credentialed"),
        )
        for url, message in cases:
            with self.subTest(url=url):
                with self.assertRaisesRegex(ModelRetry, message):
                    await fetch(url)

    def test_uses_pydantic_ssrf_safe_bounded_fetcher(self) -> None:
        fetch = _web_fetch_tool().function
        upstream = fetch.__closure__[0].cell_contents

        self.assertFalse(upstream.function.__self__.allow_local_urls)
        self.assertEqual(upstream.function.__self__.timeout, 30)
        self.assertIsNone(upstream.function.__self__.max_content_length)
        self.assertEqual(upstream.function.__self__.max_download_bytes, 5 * 1024 * 1024)
        self.assertIsNone(upstream.function.__self__.headers)

    async def test_success_marks_content_untrusted_and_records_fetch_audit(self) -> None:
        async def fake_fetch(url: str):
            return {
                "url": url,
                "title": "Example",
                "content": "body\n\n[Content truncated]",
            }

        with patch(
            "deep_agent.web.runtime.web_fetch_tool",
            return_value=Tool(fake_fetch),
        ):
            result = await _web_fetch_tool().function("https://example.com/page")

        self.assertIsInstance(result, ToolReturn)
        self.assertTrue(result.return_value["untrusted_external_content"])
        self.assertTrue(result.metadata["web_content_untrusted"])
        self.assertTrue(result.metadata["web_fetch"]["truncated"])
        self.assertEqual(result.metadata["web_fetch"]["attempts"], 1)
        self.assertFalse(result.metadata["web_fetch"]["retried"])
        self.assertIsNone(result.metadata["web_fetch"]["retry_reason"])
        self.assertEqual(result.metadata["web_fetch"]["retry_wait_ms"], 0)
        self.assertEqual(
            result.metadata["sources"][0]["url"],
            "https://example.com/page",
        )

    async def test_transport_retry_becomes_terminal_tool_feedback(self) -> None:
        fetch_calls = 0

        async def fake_fetch(url: str):
            nonlocal fetch_calls
            fetch_calls += 1
            request = httpx2.Request("GET", url)
            try:
                raise httpx2.ConnectTimeout("proxy timeout", request=request)
            except httpx2.ConnectTimeout as cause:
                raise ModelRetry("Unable to fetch URL") from cause

        with (
            patch(
                "deep_agent.web.runtime.web_fetch_tool",
                return_value=Tool(fake_fetch),
            ),
            patch(
                "deep_agent.web.runtime.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep,
        ):
            with self.assertRaisesRegex(ToolFailed, "timed out"):
                await _web_fetch_tool().function("https://example.com/page")

        self.assertEqual(fetch_calls, 2)
        sleep.assert_awaited_once_with(0.5)

    async def test_transient_timeout_retries_once_then_records_success(self) -> None:
        fetch_calls = 0

        async def fake_fetch(url: str):
            nonlocal fetch_calls
            fetch_calls += 1
            if fetch_calls == 1:
                request = httpx2.Request("GET", url)
                try:
                    raise httpx2.ReadTimeout("read timeout", request=request)
                except httpx2.ReadTimeout as cause:
                    raise ModelRetry("Unable to fetch URL") from cause
            return {"url": url, "title": "Recovered", "content": "body"}

        with (
            patch(
                "deep_agent.web.runtime.web_fetch_tool",
                return_value=Tool(fake_fetch),
            ),
            patch(
                "deep_agent.web.runtime.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep,
        ):
            result = await _web_fetch_tool().function("https://example.com/page")

        self.assertEqual(fetch_calls, 2)
        sleep.assert_awaited_once_with(0.5)
        self.assertEqual(result.metadata["web_fetch"]["attempts"], 2)
        self.assertTrue(result.metadata["web_fetch"]["retried"])
        self.assertEqual(result.metadata["web_fetch"]["retry_reason"], "timeout")
        self.assertEqual(result.metadata["web_fetch"]["retry_wait_ms"], 500)

    async def test_retryable_http_status_honors_short_retry_after(self) -> None:
        fetch_calls = 0

        async def fake_fetch(url: str):
            nonlocal fetch_calls
            fetch_calls += 1
            if fetch_calls == 1:
                request = httpx2.Request("GET", url)
                response = httpx2.Response(
                    503,
                    request=request,
                    headers={"Retry-After": "1"},
                )
                try:
                    raise httpx2.HTTPStatusError(
                        "Service unavailable",
                        request=request,
                        response=response,
                    )
                except httpx2.HTTPStatusError as cause:
                    raise ModelRetry("Unable to fetch URL") from cause
            return {"url": url, "title": "Recovered", "content": "body"}

        with (
            patch(
                "deep_agent.web.runtime.web_fetch_tool",
                return_value=Tool(fake_fetch),
            ),
            patch(
                "deep_agent.web.runtime.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep,
        ):
            result = await _web_fetch_tool().function("https://example.com/page")

        self.assertEqual(fetch_calls, 2)
        sleep.assert_awaited_once_with(1.0)
        self.assertEqual(result.metadata["web_fetch"]["retry_reason"], "http_503")
        self.assertEqual(result.metadata["web_fetch"]["retry_wait_ms"], 1000)

    async def test_non_retryable_http_status_fails_without_retry(self) -> None:
        fetch_calls = 0

        async def fake_fetch(url: str):
            nonlocal fetch_calls
            fetch_calls += 1
            request = httpx2.Request("GET", url)
            response = httpx2.Response(404, request=request)
            try:
                raise httpx2.HTTPStatusError(
                    "Not found",
                    request=request,
                    response=response,
                )
            except httpx2.HTTPStatusError as cause:
                raise ModelRetry("Unable to fetch URL") from cause

        with (
            patch(
                "deep_agent.web.runtime.web_fetch_tool",
                return_value=Tool(fake_fetch),
            ),
            patch(
                "deep_agent.web.runtime.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep,
        ):
            with self.assertRaisesRegex(ToolFailed, "HTTP 404"):
                await _web_fetch_tool().function("https://example.com/missing")

        self.assertEqual(fetch_calls, 1)
        sleep.assert_not_awaited()

    async def test_long_retry_after_fails_without_blocking_run(self) -> None:
        fetch_calls = 0

        async def fake_fetch(url: str):
            nonlocal fetch_calls
            fetch_calls += 1
            request = httpx2.Request("GET", url)
            response = httpx2.Response(
                429,
                request=request,
                headers={"Retry-After": "30"},
            )
            try:
                raise httpx2.HTTPStatusError(
                    "Rate limited",
                    request=request,
                    response=response,
                )
            except httpx2.HTTPStatusError as cause:
                raise ModelRetry("Unable to fetch URL") from cause

        with (
            patch(
                "deep_agent.web.runtime.web_fetch_tool",
                return_value=Tool(fake_fetch),
            ),
            patch(
                "deep_agent.web.runtime.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep,
        ):
            with self.assertRaisesRegex(ToolFailed, "HTTP 429"):
                await _web_fetch_tool().function("https://example.com/page")

        self.assertEqual(fetch_calls, 1)
        sleep.assert_not_awaited()

    async def test_tls_certificate_failure_is_not_retried(self) -> None:
        fetch_calls = 0

        async def fake_fetch(_url: str):
            nonlocal fetch_calls
            fetch_calls += 1
            try:
                raise ssl.SSLCertVerificationError(1, "certificate verify failed")
            except ssl.SSLCertVerificationError as cause:
                raise ModelRetry("Unable to fetch URL") from cause

        with (
            patch(
                "deep_agent.web.runtime.web_fetch_tool",
                return_value=Tool(fake_fetch),
            ),
            patch(
                "deep_agent.web.runtime.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep,
        ):
            with self.assertRaisesRegex(ToolFailed, "TLS certificate"):
                await _web_fetch_tool().function("https://example.com/page")

        self.assertEqual(fetch_calls, 1)
        sleep.assert_not_awaited()

    async def test_cancellation_propagates_without_retry(self) -> None:
        fetch_calls = 0

        async def fake_fetch(_url: str):
            nonlocal fetch_calls
            fetch_calls += 1
            raise asyncio.CancelledError

        with (
            patch(
                "deep_agent.web.runtime.web_fetch_tool",
                return_value=Tool(fake_fetch),
            ),
            patch(
                "deep_agent.web.runtime.asyncio.sleep",
                new=AsyncMock(),
            ) as sleep,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await _web_fetch_tool().function("https://example.com/page")

        self.assertEqual(fetch_calls, 1)
        sleep.assert_not_awaited()

    async def test_non_transport_retry_keeps_upstream_retry_semantics(self) -> None:
        async def fake_fetch(_url: str):
            raise ModelRetry("URL resolves to a private/internal address")

        with patch(
            "deep_agent.web.runtime.web_fetch_tool",
            return_value=Tool(fake_fetch),
        ):
            with self.assertRaisesRegex(ModelRetry, "private/internal"):
                await _web_fetch_tool().function("https://example.com/page")


class WebImageViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_small_native_image_url_reference(self) -> None:
        with patch(
            "deep_agent.web.runtime.validate_and_resolve_url",
            new=AsyncMock(return_value=SimpleNamespace()),
        ):
            result = await _web_image_view_tool().function(
                "https://cdn.example.com/chart.png"
            )

        self.assertIsInstance(result, ToolReturn)
        self.assertIsInstance(result.return_value, ImageUrl)
        self.assertEqual(result.return_value.url, "https://cdn.example.com/chart.png")
        self.assertEqual(result.return_value.media_type, "image/png")
        self.assertFalse(result.return_value.force_download)
        self.assertNotIn("data:", str(result.return_value))
        self.assertEqual(result.metadata["web_image_view"]["media_type"], "image/png")

    async def test_force_download_is_deferred_to_model_request_mapping(self) -> None:
        with patch(
            "deep_agent.web.runtime.validate_and_resolve_url",
            new=AsyncMock(return_value=SimpleNamespace()),
        ):
            result = await _web_image_view_tool().function(
                "https://cdn.example.com/chart.webp",
                force_download=True,
            )

        self.assertTrue(result.return_value.force_download)
        self.assertEqual(result.return_value.media_type, "image/webp")

    async def test_extensionless_image_uses_bounded_content_probe(self) -> None:
        with (
            patch(
                "deep_agent.web.runtime.validate_and_resolve_url",
                new=AsyncMock(return_value=SimpleNamespace()),
            ),
            patch(
                "deep_agent.web.runtime._probe_image_media_type",
                new=AsyncMock(return_value="image/jpeg"),
            ) as probe,
        ):
            result = await _web_image_view_tool().function(
                "https://images.example.com/render?id=42"
            )

        probe.assert_awaited_once_with("https://images.example.com/render?id=42")
        self.assertEqual(result.return_value.media_type, "image/jpeg")

    async def test_rejects_non_image_credentials_and_private_urls(self) -> None:
        with self.assertRaisesRegex(ModelRetry, "credentialed"):
            await _web_image_view_tool().function(
                "https://user:secret@example.com/chart.png"
            )

        with patch(
            "deep_agent.web.runtime.validate_and_resolve_url",
            new=AsyncMock(side_effect=ValueError("private/internal address")),
        ):
            with self.assertRaisesRegex(ModelRetry, "private/internal"):
                await _web_image_view_tool().function("http://127.0.0.1/chart.png")

        with patch(
            "deep_agent.web.runtime.validate_and_resolve_url",
            new=AsyncMock(return_value=SimpleNamespace()),
        ):
            with self.assertRaisesRegex(ModelRetry, "text/html"):
                await _web_image_view_tool().function(
                    "https://example.com/not-an-image.html"
                )


class ProviderSearchRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_pydantic_search_rows_are_normalized_from_typed_dicts(self) -> None:
        async def fake_duck(query: str):
            return [{"title": "Duck", "href": "https://duck.example", "body": query}]

        async def fake_tavily(query: str):
            return [{
                "title": "Tavily",
                "url": "https://tavily.example",
                "content": query,
                "score": 1.0,
            }]

        with patch(
            "deep_agent.web.runtime.duckduckgo_search_tool",
            return_value=Tool(fake_duck),
        ):
            duck = await _search_duckduckgo("query")
        with patch(
            "deep_agent.web.runtime.tavily_search_tool",
            return_value=Tool(fake_tavily),
        ):
            tavily = await _search_tavily(
                WebSearchRuntimeConfig(
                    mode="provider",
                    provider="tavily",
                    api_key="secret",
                ),
                "query",
            )

        self.assertEqual(duck.metadata["sources"][0]["url"], "https://duck.example")
        self.assertEqual(tavily.metadata["sources"][0]["url"], "https://tavily.example")

    def test_native_deepseek_sources_come_from_structured_tool_result(self) -> None:
        class FakeResult:
            @staticmethod
            def all_messages():
                return [
                    ModelResponse(
                        parts=[
                            NativeToolReturnPart(
                                "web_search",
                                {
                                    "status": "completed",
                                    "sources": [
                                        {
                                            "url": "https://example.com/source",
                                            "title": "Example",
                                        }
                                    ],
                                },
                                "native-search-1",
                                provider_name="deepseek",
                            )
                        ]
                    )
                ]

        self.assertEqual(
            _native_result_sources(FakeResult()),
            [{"url": "https://example.com/source", "title": "Example"}],
        )

    async def test_provider_failure_never_falls_back(self) -> None:
        config = WebSearchRuntimeConfig(
            mode="provider",
            provider="tavily",
            api_key="secret",
        )
        with (
            patch(
                "deep_agent.web.runtime._search_tavily",
                new=AsyncMock(side_effect=TimeoutError()),
            ) as tavily,
            patch(
                "deep_agent.web.runtime._search_duckduckgo",
                new=AsyncMock(),
            ) as duckduckgo,
        ):
            with self.assertRaisesRegex(ToolFailed, "timed out.*provider=tavily"):
                await execute_provider_search(config, "Pydantic AI")

        tavily.assert_awaited_once()
        duckduckgo.assert_not_awaited()

    async def test_empty_query_fails_before_provider_call(self) -> None:
        config = WebSearchRuntimeConfig(mode="provider", provider="duckduckgo")
        with patch(
            "deep_agent.web.runtime._search_duckduckgo",
            new=AsyncMock(),
        ) as search:
            with self.assertRaisesRegex(ToolFailed, "cannot be empty"):
                await execute_provider_search(config, "   ")
        search.assert_not_awaited()

    async def test_success_records_provider_elapsed_and_sources(self) -> None:
        config = WebSearchRuntimeConfig(mode="provider", provider="duckduckgo")
        upstream = ToolReturn(
            return_value={"result_count": 1},
            metadata={"sources": [{"url": "https://example.com"}]},
        )
        with patch(
            "deep_agent.web.runtime._search_duckduckgo",
            new=AsyncMock(return_value=upstream),
        ):
            result = await execute_provider_search(config, "Pydantic AI")

        self.assertEqual(result.metadata["web_search"]["provider"], "duckduckgo")
        self.assertEqual(result.metadata["web_search"]["mode"], "provider")
        self.assertGreaterEqual(result.metadata["web_search"]["elapsed_ms"], 0)
        self.assertEqual(result.metadata["sources"][0]["url"], "https://example.com")


if __name__ == "__main__":
    unittest.main()
