"""Real OpenAI wire serialization for Moonshot Formula and encrypted replay."""
import json
import unittest
from unittest.mock import patch

import httpx2
from openai import AsyncOpenAI
from pydantic_ai import Agent, ToolFailed
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from deep_agent.models.provider_registry import ProviderRuntimeConfig
from deep_agent.web.kimi import execute_search
from deep_agent.web.runtime import build_web_runtime, execute_provider_search, probe_web_search_config
from deep_agent.web.settings import WebSearchRuntimeConfig

OUTPUT = "----MOONSHOT ENCRYPTED BEGIN----fixture----MOONSHOT ENCRYPTED END----"
TOOL = {"type": "function", "function": {"name": "web_search", "parameters": {
    "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}


class KimiFormulaTests(unittest.IsolatedAsyncioTestCase):
    async def run_loop(self, current_model, probe_only=False):
        requests = []
        chat_count = 0

        def respond(request):
            nonlocal chat_count
            body = json.loads(request.content) if request.content else None
            requests.append((request.url.path, body))
            if request.url.path.endswith('/tools'):
                return httpx2.Response(200, json={"tools": [TOOL]})
            if request.url.path.endswith('/fibers'):
                self.assertEqual(body['name'], 'web_search')
                self.assertEqual(json.loads(body['arguments']), {'query': 'fixture search'})
                return httpx2.Response(200, json={"status": "succeeded", "context": {"encrypted_output": OUTPUT}})
            self.assertNotEqual(body.get('tool_choice'), 'required')
            chat_count += 1
            names = [t['function']['name'] for t in body['tools']]
            self.assertEqual(names.count('web_search'), 1)
            self.assertNotIn('$web_search', names)
            if chat_count == 1:
                message = {"role": "assistant", "content": None,
                           "reasoning_content": "Need a search.", "tool_calls": [{
                               "id": "search-1", "type": "function", "function": {
                                   "name": "web_search", "arguments": '{"query": "fixture search"}'}}]}
                finish = 'tool_calls'
            else:
                self.assertEqual(body['messages'][-1]['content'], OUTPUT)
                self.assertEqual(body['messages'][-1]['tool_call_id'], 'search-1')
                self.assertEqual(body['messages'][-2]['tool_calls'][0]['id'], 'search-1')
                message = {"role": "assistant", "content": "Found https://example.org/docs"}
                finish = 'stop'
            return httpx2.Response(200, json={"id": "fixture", "object": "chat.completion", "created": 1,
                "model": "kimi-k3", "choices": [{"index": 0, "finish_reason": finish, "message": message}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
            def client(*args):
                return AsyncOpenAI(api_key='fixture', base_url='https://moonshot.invalid/v1', http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)))
            with patch('deep_agent.web.runtime.search_client', side_effect=client):
                if current_model:
                    provider = ProviderRuntimeConfig(id='fixture', name='fixture', provider_type='moonshotai',
                        use_default_base_url=False, base_url='https://moonshot.invalid/v1', api_key='fixture')
                    runtime = build_web_runtime(WebSearchRuntimeConfig(mode='native'), current_provider=provider)
                    self.assertIsNone(runtime.capability)
                    model = OpenAIChatModel('kimi-k3', provider=OpenAIProvider(api_key='fixture', base_url='https://moonshot.invalid/v1', http_client=http))
                    result = await Agent(model, toolsets=[runtime.toolset]).run('fixture')
                    self.assertIn('https://example.org/docs', result.output)
                elif probe_only:
                    await probe_web_search_config(WebSearchRuntimeConfig(mode="provider", provider="kimi",
                        model="kimi-k3", api_key="fixture"))
                else:
                    result = await execute_provider_search(WebSearchRuntimeConfig(mode='provider', provider='kimi',
                        model='kimi-k3', api_key='fixture'), 'fixture')
                    self.assertIn('https://example.org/docs', result.return_value['content'])
        self.assertEqual(chat_count, 1 if probe_only else 2)
        self.assertEqual(sum(path.endswith('/fibers') for path, _ in requests), 1)

    async def test_fixed_provider_replays_formula_result_verbatim(self):
        await self.run_loop(False)

    async def test_current_model_runs_formula_in_same_agent(self):
        await self.run_loop(True)

    async def test_failed_or_empty_fiber_is_not_success(self):
        for payload in ({'status': 'failed', 'context': {'output': 'partial'}},
                        {'status': 'succeeded', 'context': {}}):
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, json=payload))) as http:
                async with AsyncOpenAI(api_key='fixture', base_url='https://moonshot.invalid/v1', http_client=http) as client:
                    with self.assertRaises(ToolFailed):
                        await execute_search(client, '{"query":"fixture"}')
