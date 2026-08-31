from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic_ai import ModelRetry, Tool, ToolFailed
from pydantic_ai.messages import ModelResponse, NativeToolReturnPart, ToolReturn
from pydantic_ai.capabilities import WebSearch
from pydantic_ai.toolsets import CombinedToolset, FunctionToolset

from deep_agent.code_mode.file_payload import CODE_MODE_HYBRID, CODE_MODE_METADATA_KEY
from deep_agent.web.package import build_web_tool_package
from deep_agent.web.runtime import (
    _search_duckduckgo,
    _search_tavily,
    _web_fetch_tool,
    _native_result_sources,
    build_web_runtime,
    execute_provider_search,
)
from deep_agent.web.settings import WebSearchRuntimeConfig, load_web_search_config


class WebPackageTests(unittest.TestCase):
    def test_builtin_package_exposes_exactly_search_and_fetch(self) -> None:
        package = build_web_tool_package()

        self.assertEqual(package.code, "builtin:web")
        self.assertEqual(package.label, "Web")
        self.assertEqual(
            [tool.name for tool in package.tools],
            ["web_search", "web_fetch"],
        )

    def test_native_mode_keeps_fetch_local_and_search_native(self) -> None:
        runtime = build_web_runtime(WebSearchRuntimeConfig(mode="native"))

        self.assertIsInstance(runtime.toolset, FunctionToolset)
        self.assertEqual(set(runtime.toolset.tools), {"web_fetch"})
        self.assertIsInstance(runtime.capability, WebSearch)
        fetch = runtime.toolset.tools["web_fetch"]
        self.assertEqual(fetch.metadata[CODE_MODE_METADATA_KEY], CODE_MODE_HYBRID)
        self.assertTrue(fetch.metadata["web_content_untrusted"])

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
        self.assertEqual(search.metadata[CODE_MODE_METADATA_KEY], CODE_MODE_HYBRID)


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
        self.assertEqual(upstream.function.__self__.max_content_length, 50_000)
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
        self.assertEqual(
            result.metadata["sources"][0]["url"],
            "https://example.com/page",
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
