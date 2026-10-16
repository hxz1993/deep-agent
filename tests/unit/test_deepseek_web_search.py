import json
import unittest
from unittest.mock import patch
import httpx2
from pydantic_ai import ModelRetry, ToolFailed
from deep_agent.web.deepseek import execute_search, parse_search_results, search_endpoint
from deep_agent.web.runtime import execute_provider_search, probe_web_search_config
from deep_agent.web.settings import WebSearchRuntimeConfig


def payload():
    return {"results": [{"url": "https://example.org/docs", "title": "Fixture",
                         "summary": "source excerpt", "snippet": "short", "date": None, "authority": -1}]}


class DeepSeekSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_and_probe_use_standalone_api_not_chat(self):
        original = httpx2.AsyncClient
        requests = []
        def respond(request):
            body = json.loads(request.content)
            requests.append(request)
            self.assertEqual(str(request.url), "https://api.deepseek.com/v1/tools/search")
            self.assertEqual(set(body), {"query", "count", "need_content"})
            self.assertEqual(body["count"], 5)
            self.assertFalse(body["need_content"])
            self.assertEqual(request.headers["authorization"], "Bearer fixture")
            return httpx2.Response(200, json=payload())
        with patch("deep_agent.web.provider_api.httpx2.AsyncClient", side_effect=lambda **kw: original(**kw, transport=httpx2.MockTransport(respond))):
            config = WebSearchRuntimeConfig(mode="provider", provider="deepseek", api_key="fixture", model="obsolete")
            result = await execute_provider_search(config, "query")
            await probe_web_search_config(config)
        self.assertEqual(len(requests), 2)
        self.assertIn("source excerpt", result.return_value["content"])
        self.assertEqual(result.return_value["result_count"], 1)

    def test_response_validation_and_empty_results(self):
        self.assertEqual(parse_search_results({"results": []}), [])
        for value in ({"content": [{"type": "text", "text": "fake answer"}]}, {},
                      {"results": [{}]}, {"results": [{"url": "javascript:bad"}]}):
            with self.subTest(value=value), self.assertRaises(ToolFailed):
                parse_search_results(value)

    def test_endpoint_keeps_custom_gateway(self):
        self.assertEqual(search_endpoint("https://gateway.example/prefix/v1"), "https://gateway.example/prefix/v1/tools/search")
        for url in (None, "https://api.deepseek.com", "https://api.deepseek.com/v1", "https://api.deepseek.com/anthropic"):
            self.assertEqual(search_endpoint(url), "https://api.deepseek.com/v1/tools/search")
        with self.assertRaises(ToolFailed):
            search_endpoint("https://key@gateway.example/v1")

    async def test_redirect_does_not_forward_credentials_and_empty_is_success(self):
        original = httpx2.AsyncClient
        for status in (302, 200):
            requests = []
            def respond(request):
                requests.append(request)
                return httpx2.Response(status, headers={"location": "https://other.example"}, json={"results": []})
            with patch("deep_agent.web.provider_api.httpx2.AsyncClient", side_effect=lambda **kw: original(**kw, transport=httpx2.MockTransport(respond))):
                config = WebSearchRuntimeConfig(mode="provider", provider="deepseek", api_key="fixture")
                if status == 302:
                    with self.assertRaises(ToolFailed):
                        await execute_provider_search(config, "query")
                else:
                    result = await execute_provider_search(config, "query")
                    self.assertEqual(result.return_value["result_count"], 0)
                    self.assertIn("未找到", result.return_value["content"])
            self.assertEqual(len(requests), 1)

    async def test_query_limit_is_not_silently_truncated(self):
        with self.assertRaises(ModelRetry):
            await execute_search(api_key="fixture", base_url=None, query="中" * 129)
