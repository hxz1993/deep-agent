import json
import unittest
from unittest.mock import patch
import httpx2
from pydantic_ai import ToolFailed
from deep_agent.web.deepseek import parse_search_results, search_endpoint
from deep_agent.web.runtime import execute_provider_search, probe_web_search_config
from deep_agent.web.settings import WebSearchRuntimeConfig


def payload():
    return {'content': [
        {'type': 'web_search_tool_result', 'content': [
            {'type': 'web_search_result', 'url': 'https://example.org/docs', 'title': 'Fixture', 'page_age': 'today'},
            {'type': 'web_search_result', 'url': 'https://example.org/docs', 'title': 'Duplicate'}]},
        {'type': 'text', 'text': 'Do not pass this generated answer', 'citations': [
            {'url': 'https://example.org/docs', 'cited_text': 'source excerpt'}]}]}


class DeepSeekSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_and_probe_use_messages_not_responses(self):
        original = httpx2.AsyncClient
        requests = []
        def respond(request):
            body = json.loads(request.content)
            requests.append(request)
            self.assertEqual(str(request.url), 'https://api.deepseek.com/anthropic/v1/messages')
            self.assertEqual(body['model'], 'deepseek-flash')
            self.assertEqual(body['tools'][0]['type'], 'web_search_20250305')
            self.assertEqual(request.headers['x-api-key'], 'fixture')
            return httpx2.Response(200, json=payload())
        def client(**kwargs):
            self.assertFalse(kwargs['follow_redirects'])
            return original(**kwargs, transport=httpx2.MockTransport(respond))
        config = WebSearchRuntimeConfig(mode='provider', provider='deepseek', api_key='fixture', base_url='https://api.deepseek.com')
        with patch('deep_agent.web.deepseek.httpx2.AsyncClient', side_effect=client):
            result = await execute_provider_search(config, 'query')
            await probe_web_search_config(config)
        self.assertEqual(len(requests), 2)
        self.assertIn('source excerpt', result.return_value['content'])
        self.assertNotIn('generated answer', result.return_value['content'])
        self.assertEqual(result.return_value['result_count'], 1)

    def test_structured_evidence_required(self):
        for value in ({'content': [{'type': 'text', 'text': '<tool_use>web_search https://example.org</tool_use>'}]},
                      {'content': [{'type': 'web_search_tool_result', 'content': []}]},
                      {'content': [{'type': 'web_search_tool_result', 'content': {'type': 'web_search_tool_result_error', 'error_code': 'unavailable'}}]}):
            with self.subTest(value=value), self.assertRaises(ToolFailed):
                parse_search_results(value)

    def test_endpoint_keeps_custom_gateway(self):
        self.assertEqual(search_endpoint('https://gateway.example/prefix/v1'), 'https://gateway.example/prefix/v1/messages')
        self.assertEqual(search_endpoint('https://api.deepseek.com/v1'), 'https://api.deepseek.com/anthropic/v1/messages')
        with self.assertRaises(ToolFailed):
            search_endpoint('https://key@gateway.example/v1')

    async def test_redirect_does_not_forward_credentials(self):
        original = httpx2.AsyncClient
        requests = []
        def respond(request):
            requests.append(str(request.url))
            return httpx2.Response(302, headers={'location': 'https://other.example/messages'})
        with patch('deep_agent.web.deepseek.httpx2.AsyncClient', side_effect=lambda **kw: original(**kw, transport=httpx2.MockTransport(respond))):
            with self.assertRaises(ToolFailed):
                await execute_provider_search(WebSearchRuntimeConfig(mode='provider', provider='deepseek', api_key='fixture'), 'query')
        self.assertEqual(len(requests), 1)
