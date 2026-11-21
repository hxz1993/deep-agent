from __future__ import annotations

import asyncio
import json
import ssl
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx2
from pydantic_ai import Agent, ModelRetry, Tool, ToolFailed
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturn, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.toolsets import CombinedToolset, FunctionToolset

from deep_agent.ptc.file_payload import CODE_MODE_METADATA_KEY
from deep_agent.web.package import build_web_tool_package
from deep_agent.web.runtime import (
    _html_to_markdown,
    _web_fetch_tool,
    build_web_runtime,
    execute_provider_search,
)
from deep_agent.web.settings import WebSearchRuntimeConfig, load_web_search_config


class WebPackageTests(unittest.TestCase):
    def test_builtin_package_exposes_search_and_fetch_only(self) -> None:
        package = build_web_tool_package()

        self.assertEqual(package.code, "builtin:web")
        self.assertEqual(package.label, "Web")
        self.assertEqual(
            [tool.name for tool in package.tools],
            ["web_search", "web_fetch"],
        )
        self.assertEqual(
            [tool.display_name for tool in package.tools],
            ["网络搜索", "获取网页内容"],
        )

    def test_unconfigured_search_is_still_disclosed(self) -> None:
        runtime = build_web_runtime(WebSearchRuntimeConfig(mode="unconfigured"))
        names = {name for part in runtime.toolset.toolsets for name in part.tools}
        self.assertEqual(names, {"web_search", "web_fetch"})

    def test_visual_model_no_longer_registers_legacy_image_tool(self) -> None:
        runtime = build_web_runtime(WebSearchRuntimeConfig(mode="unconfigured"), image_capable=True)
        names = {name for part in runtime.toolset.toolsets for name in part.tools}
        self.assertEqual(names, {"web_search", "web_fetch"})

    def test_provider_mode_exposes_stable_search_and_fetch_tools(self) -> None:
        runtime = build_web_runtime(
            WebSearchRuntimeConfig(mode="provider", provider="duckduckgo")
        )

        self.assertIsInstance(runtime.toolset, CombinedToolset)
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
                    use_default_base_url=True,
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

    async def test_fetch_keeps_json_and_lets_the_model_limit_preview_length(self) -> None:
        url = "https://example.com/data"
        body = '{\n  "example": [ 1, 2, 3 ]\n\n}\n'
        response = httpx2.Response(200, headers={"content-type": "application/problem+json; charset=utf-8"},
                                   text=body, request=httpx2.Request("GET", url))
        tool = _web_fetch_tool()
        self.assertIn("max_content_length", tool.tool_def.parameters_json_schema["properties"])
        with patch("deep_agent.web.runtime.safe_download", new=AsyncMock(return_value=response)) as download:
            result = await tool.function(url)
            preview = await tool.function(url, max_content_length=12)
        self.assertEqual(result.return_value["content"], body)
        self.assertFalse(result.metadata["web_fetch"]["truncated"])
        self.assertEqual(preview.return_value["content"], body[:12] + "\n\n[Content truncated]")
        self.assertTrue(preview.metadata["web_fetch"]["truncated"])
        self.assertFalse(download.call_args.kwargs["allow_local"])
        self.assertEqual(download.call_args.kwargs["timeout"], 30)
        self.assertEqual(download.call_args.kwargs["max_bytes"], 50 * 1024 * 1024)

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
        self.assertTrue(result.metadata["web_content_untrusted"])
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


class ProviderSearchRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_duckduckgo_returns_library_rows_and_uses_selected_engine(self) -> None:
        rows = [{"title": "Duck", "href": "https://duck.example", "body": "source", "extra": 4}]
        config = WebSearchRuntimeConfig(mode="provider", provider="duckduckgo")
        search = build_web_runtime(config).toolset.toolsets[0].tools["web_search"]
        self.assertIn("region", search.tool_def.parameters_json_schema["properties"])
        with patch("ddgs.DDGS") as client:
            client.return_value.text.return_value = rows
            result = await search.function("fixture", max_results=8, region="uk-en", page=2)
        client.return_value.text.assert_called_once_with(
            "fixture", max_results=8, backend="duckduckgo", region="uk-en", page=2,
            timelimit=None, safesearch="moderate",
        )
        self.assertEqual(result.return_value, rows)
        self.assertEqual(result.metadata["sources"], [{"url": "https://duck.example", "title": "Duck"}])

    async def test_http_suppliers_use_native_schema_and_return_unmodified_payloads(self) -> None:
        original = httpx2.AsyncClient
        url = "https://example.org/docs"
        row = {"title": "Fixture", "url": url, "snippet": "source"}
        cases = [
            ("tavily", "https://api.tavily.com/search", "authorization", "Bearer fixture",
             {"max_results": 7, "include_raw_content": "text", "topic": "news"},
             {"results": [{**row, "raw_content": "article", "score": 0.99}], "answer": "Answer"}),
            ("exa", "https://api.exa.ai/search", "x-api-key", "fixture",
             {"numResults": 7, "contents": {"text": True, "highlights": True}, "includeDomains": ["example.org"]},
             {"results": [{**row, "text": "article", "highlights": ["Line"]}], "requestId": "exa-1"}),
            ("you", "https://ydc-index.io/v1/search", "x-api-key", "fixture",
             {"count": 7, "extraction": {"extraction_mode": "full_page"}, "freshness": "month"},
             {"results": {"news": [{**row, "contents": {"markdown": "article"}}]}, "metadata": {"search_uuid": "you-1"}}),
        ]
        for provider, endpoint, header, credential, options, payload in cases:
            with self.subTest(provider=provider):
                args = {"query": "search terms", **options}
                requests = []

                def respond(request):
                    self.assertEqual(str(request.url), endpoint)
                    self.assertEqual(request.method, "POST")
                    self.assertEqual(request.headers[header], credential)
                    requests.append(json.loads(request.content))
                    return httpx2.Response(200, json=payload)

                def model(messages, info):
                    search = next(tool for tool in info.function_tools if tool.name == "web_search")
                    schema = search.parameters_json_schema
                    self.assertEqual(schema["required"], ["query"])
                    self.assertTrue(set(args) <= set(schema["properties"]))
                    returns = [part for message in messages for part in message.parts
                               if isinstance(part, ToolReturnPart)]
                    if not returns:
                        return ModelResponse(parts=[ToolCallPart("web_search", args, "search")])
                    self.assertEqual(returns[0].content, payload)
                    self.assertEqual(returns[0].metadata["sources"], [{"title": "Fixture", "url": url}])
                    return ModelResponse(parts=[TextPart("done")])

                config = WebSearchRuntimeConfig(mode="provider", provider=provider, api_key="fixture")
                agent = Agent(FunctionModel(model), toolsets=[build_web_runtime(config).toolset])
                with patch("deep_agent.web.provider_api.httpx2.AsyncClient",
                           side_effect=lambda **kw: original(**kw, transport=httpx2.MockTransport(respond))):
                    self.assertEqual((await agent.run("search")).output, "done")
                self.assertEqual(requests, [args])

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
