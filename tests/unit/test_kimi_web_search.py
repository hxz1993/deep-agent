"""Moonshot retrieval is one search request, independent of any selected model."""
import json
import unittest
from unittest.mock import patch

import httpx2
from openai import AsyncOpenAI
from pydantic_ai import ToolFailed
from deep_agent.web.kimi import execute_search
from deep_agent.web.runtime import execute_provider_search, probe_web_search_config
from deep_agent.web.settings import WebSearchRuntimeConfig

RESULTS = [{"url": "https://example.org/docs", "title": "Fixture", "snippet": "Search result"}]


class KimiSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_and_probe_do_not_call_chat_or_pass_model(self):
        for probe in (False, True):
            requests = []
            def respond(request):
                body = json.loads(request.content)
                requests.append(request.url.path)
                self.assertEqual(request.url.path, '/v1/tools/search')
                self.assertEqual(set(body), {'text_query', 'limit', 'timeout_seconds', 'include_content'})
                self.assertEqual(request.headers['authorization'], 'Bearer fixture')
                return httpx2.Response(200, json={'search_results': RESULTS})
            def client(*args):
                return AsyncOpenAI(api_key='fixture', base_url='https://moonshot.invalid/v1',
                    http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)))
            config = WebSearchRuntimeConfig(mode='provider', provider='kimi', model='obsolete-model', api_key='fixture')
            with patch('deep_agent.web.runtime.search_client', side_effect=client):
                if probe:
                    await probe_web_search_config(config)
                else:
                    result = await execute_provider_search(config, 'fixture search')
                    self.assertIn('Search result', result.return_value['content'])
                    self.assertEqual(result.metadata['sources'][0]['url'], RESULTS[0]['url'])
            self.assertEqual(len(requests), 1)

    async def test_empty_or_invalid_search_result_is_not_success(self):
        for payload in ({}, {'search_results': []}, {'search_results': 'invalid'},
                        {'search_results': [{}]}, {'search_results': [{'url': 'javascript:bad'}]}):
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, json=payload))) as http:
                async with AsyncOpenAI(api_key='fixture', base_url='https://moonshot.invalid/v1', http_client=http) as client:
                    with self.assertRaises(ToolFailed):
                        await execute_search(client, 'fixture')
