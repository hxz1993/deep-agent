import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx2
from pydantic_ai import Agent, ModelRetry, ToolFailed
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from deep_agent.agent.deps import AgentDeps
from deep_agent.web.deepseek import execute_search, parse_search_results, search_endpoint
from deep_agent.web.runtime import build_web_runtime, execute_provider_search, probe_web_search_config
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
        self.assertEqual(result.return_value, payload())
        self.assertEqual(result.metadata["sources"], [{"url": "https://example.org/docs", "title": "Fixture"}])
        self.assertFalse(result.metadata["web_search"]["truncated"])

    async def test_deepseek_schema_and_native_result_reach_model(self):
        response = {
            "results": [
                {**payload()["results"][0], "content": f"page {index}"}
                for index in range(6)
            ],
            "extra": {"vendor_field": True},
        }
        query = "site:python.org 新版本"
        question = "帮我找 Python 最新版的官方发布说明"
        explicit = {"count": 8, "need_content": True, "ori_question": "哪个版本最新？",
                    "localization": "203.0.113.11"}

        def model(call_args):
            def respond(messages, info):
                search = next(tool for tool in info.function_tools if tool.name == "web_search")
                schema = search.parameters_json_schema
                self.assertEqual(schema["required"], ["query"])
                self.assertEqual(set(schema["properties"]), {"query", *explicit})
                self.assertEqual(schema["properties"]["count"]["default"], 5)
                self.assertEqual(schema["properties"]["count"]["maximum"], 50)
                self.assertFalse(schema["properties"]["need_content"]["default"])
                returns = [part for message in messages for part in message.parts
                           if isinstance(part, ToolReturnPart)]
                if not returns:
                    return ModelResponse(parts=[ToolCallPart("web_search", call_args, "search")])
                self.assertEqual(returns[0].content, response)
                self.assertEqual(len(returns[0].metadata["sources"]), 1)
                return ModelResponse(parts=[TextPart("done")])

            return FunctionModel(respond)

        config = WebSearchRuntimeConfig(mode="provider", provider="deepseek", api_key="fixture")
        runtime = build_web_runtime(config)
        for supplied, user_text, expected in (
            (explicit, question, explicit),
            ({}, question, {"count": 5, "need_content": False, "ori_question": question}),
            ({}, "长" * 129, {"count": 5, "need_content": False}),
        ):
            with self.subTest(supplied=supplied, user_text_length=len(user_text)):
                agent = Agent(model({"query": query, **supplied}), deps_type=AgentDeps, toolsets=[runtime.toolset])
                deps = AgentDeps(session_id="search-test", tool_discovery_primary_query=user_text)
                with patch("deep_agent.web.deepseek.post_json", new=AsyncMock(return_value=response)) as post:
                    result = await agent.run(user_text, deps=deps)
                self.assertEqual(result.output, "done")
                post.assert_awaited_once_with(
                    "https://api.deepseek.com/v1/tools/search", "fixture", {"query": query, **expected},
                )

    async def test_other_provider_tool_does_not_inherit_deepseek_schema(self):
        def respond(_messages, info):
            search = next(tool for tool in info.function_tools if tool.name == "web_search")
            self.assertEqual(set(search.parameters_json_schema["properties"]),
                             {"query", "max_results", "region", "safesearch", "timelimit", "page"})
            return ModelResponse(parts=[TextPart("done")])

        config = WebSearchRuntimeConfig(mode="provider", provider="duckduckgo")
        agent = Agent(FunctionModel(respond), toolsets=[build_web_runtime(config).toolset])
        await agent.run("show available search parameters")

    def test_response_validation_and_empty_results(self):
        self.assertEqual(parse_search_results({"results": []}), {"results": []})
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
                    self.assertEqual(result.return_value, {"results": []})
            self.assertEqual(len(requests), 1)

    async def test_query_limit_is_not_silently_truncated(self):
        with self.assertRaises(ModelRetry):
            await execute_search(api_key="fixture", base_url=None, query="中" * 129)
