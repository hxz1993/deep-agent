"""Exercise the real SDK wire mapper, not a mocked model implementation."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import httpx2
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    ModelRequest, ModelResponse, ToolSearchCallPart, ToolSearchReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.native_tools import WebSearchTool
from pydantic_ai.native_tools._tool_search import ToolSearchTool
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.tools import Tool, ToolDefinition

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_discovery import DeepAgentToolSearch
from deep_agent.models.provider_registry import ProviderRuntimeConfig, resolve_provider_model


DISCOVERED = ["deepcube_search", "deepfos-doc_docs_search"]


def config(provider_type="deepseek", *, custom=False):
    return ProviderRuntimeConfig(
        id="fixture", name="fixture", provider_type=provider_type,
        api_key="fixture-only", use_default_base_url=not custom,
        base_url="https://fixture.invalid/v1",
    )


def model_for(provider_type="deepseek", *, custom=False):
    return resolve_provider_model(
        model_name="deepseek-v4-flash-vision-exp" if provider_type == "deepseek" else "gpt-5.4",
        provider=config(provider_type, custom=custom), native_web_search=provider_type != "deepseek",
    )


class DeepSeekToolSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_search_sends_any_model_to_provider_and_surfaces_api_error(self):
        for custom in (False, True):
            for model_name in ("deepseek-flash", "deepseek-v4-flash", "future-model-alias"):
                with self.subTest(custom=custom, model=model_name):
                    sent = []
                    def transport(request):
                        sent.append(json.loads(request.content))
                        return httpx2.Response(400, json={"error": {"message": "fixture provider rejected search"}})
                    async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as http:
                        runtime_provider = DeepSeekProvider(api_key="fixture", http_client=http)
                        with patch("deep_agent.models.provider_registry._openai_style_provider", return_value=runtime_provider):
                            model = resolve_provider_model(model_name=model_name, provider=config(custom=custom), native_web_search=True)
                        with self.assertRaises(ModelHTTPError):
                            await model.request([ModelRequest(parts=[UserPromptPart("search")])], None,
                                                ModelRequestParameters(native_tools=[WebSearchTool()]))
                    self.assertEqual(sent[0]["model"], model_name)
                    self.assertIn("web_search", [tool["type"] for tool in sent[0]["tools"]])

    def test_openai_keeps_native_tool_search(self):
        self.assertIn(ToolSearchTool, model_for("openai").profile["supported_native_tools"])

    async def test_replayed_search_history_does_not_redeclare_tool_schemas(self):
        sent = []

        def transport(request):
            sent.append(json.loads(request.content))
            # Stop after serialization; this is not a simulated successful model run.
            return httpx2.Response(400, json={"error": {"message": "fixture stop"}})

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
            provider = DeepSeekProvider(api_key="fixture-only", http_client=client)
            with patch("deep_agent.models.provider_registry._openai_style_provider", return_value=provider):
                model = model_for()
            params = ModelRequestParameters(
                function_tools=[ToolDefinition(name="search_tools", unless_native="tool_search")] + [
                    ToolDefinition(name=name, defer_loading=True, with_native="tool_search")
                    for name in DISCOVERED
                ],
                native_tools=[ToolSearchTool(strategy="custom")],
                revealed_tool_names=set(DISCOVERED),
            )
            history = [
                ModelRequest(parts=[UserPromptPart("fixture question")]),
                ModelResponse(parts=[ToolSearchCallPart(args={"queries": ["fixture"]}, tool_call_id="search-1")]),
                ModelRequest(parts=[ToolSearchReturnPart(tool_call_id="search-1", content={
                    "discovered_tools": [{"name": name} for name in DISCOVERED],
                })]),
            ]
            with self.assertRaises(ModelHTTPError):
                await model.request(history, None, params)
        body = sent[0]
        self.assertEqual([item["role"] for item in body["messages"]],
                         ["user", "assistant", "tool"])
        names = [item["function"]["name"] for item in body["tools"] if item["type"] == "function"]
        self.assertEqual(names, ["search_tools", *DISCOVERED])
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(item["type"] == "function" for item in body["tools"]))
        self.assertNotIn("tool_search", [item["type"] for item in body["tools"]])
        self.assertEqual(body["messages"][1]["tool_calls"][0]["id"], body["messages"][2]["tool_call_id"])
        self.assertEqual(json.loads(body["messages"][2]["content"])["discovered_tools"],
                         [{"name": name} for name in DISCOVERED])

    async def test_real_agent_loop_search_then_discovered_tool_then_answer(self):
        sent, executions = [], []

        def search_tool() -> str:
            executions.append(DISCOVERED[0])
            return "fixture search result"

        def strategy(ctx, queries, tools):
            return [DISCOVERED[0]]

        def transport(request):
            body = json.loads(request.content)
            sent.append(body)
            index = len(sent)
            if index <= 2:
                message = {"role": "assistant", "content": None, "tool_calls": [{
                    "type": "function", "id": f"call-{index}", "function": {
                        "name": "search_tools" if index == 1 else DISCOVERED[0],
                        "arguments": json.dumps({"queries": ["fixture"]} if index == 1 else {}),
                    },
                }]}
            else:
                message = {"role": "assistant", "content": "done"}
            return httpx2.Response(200, json={
                "id": f"chatcmpl-{index}", "object": "chat.completion", "created": 1,
                "model": "deepseek-flash", "choices": [{"index": 0, "message": message,
                    "finish_reason": "tool_calls" if index <= 2 else "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            })

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
            provider = DeepSeekProvider(api_key="fixture-only", http_client=client)
            with patch("deep_agent.models.provider_registry._openai_style_provider", return_value=provider):
                model = model_for()
            agent = Agent(model, deps_type=AgentDeps,
                          tools=[Tool(search_tool, name=DISCOVERED[0], defer_loading=True)],
                          capabilities=[DeepAgentToolSearch(strategy=strategy)])
            result = await agent.run("fixture question", deps=AgentDeps(session_id="fixture"))
        self.assertEqual(result.output, "done")
        self.assertEqual(executions, [DISCOVERED[0]])
        self.assertEqual(len(sent), 3)
        self.assertTrue(any(isinstance(part, ToolSearchReturnPart)
                            for message in result.all_messages() for part in message.parts))
        for body in sent:
            names = [item["function"]["name"] for item in body["tools"] if item["type"] == "function"]
            self.assertEqual(len(names), len(set(names)))
            self.assertFalse(any(item["type"] == "tool_search" for item in body["tools"]))
