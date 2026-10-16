"""Cloud Fetch uses real transport shapes and the existing tool/Agent boundary."""
import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx2
from pydantic_ai import Agent, ModelRetry, ToolFailed
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from deep_agent.web.runtime import _provider_web_fetch_tool, build_web_runtime
from deep_agent.web.settings import WebSearchRuntimeConfig
from deep_agent.web.provider_api import tool_endpoint
from deep_agent.web import proxy_compat


class ProviderFetchTests(unittest.IsolatedAsyncioTestCase):
    def config(self, provider='deepseek', **kw):
        return WebSearchRuntimeConfig(mode='provider', provider=provider,
                                     api_key='fixture-secret', fetch_mode='follow_search', **kw)

    async def test_both_supplier_shapes_keep_full_markdown_and_metadata(self):
        original = httpx2.AsyncClient
        content = '# 中文\n![image1](https://example.org/img.png)\n' + '😀尾部' * 30000
        for provider, field in [('deepseek', 'content'), ('kimi', 'markdown')]:
            requests = []
            def respond(request):
                requests.append(request)
                self.assertEqual(request.url.path, '/gateway/v1/tools/fetch')
                self.assertEqual(json.loads(request.content), {'url': 'https://example.org/page'})
                self.assertEqual(request.headers['authorization'], 'Bearer fixture-secret')
                return httpx2.Response(200, json={'title': '网页标题', field: content})
            with patch('deep_agent.web.runtime.validate_and_resolve_url', new=AsyncMock()) as validate, patch(
                'deep_agent.web.provider_api.httpx2.AsyncClient',
                side_effect=lambda **kw: original(**kw, transport=httpx2.MockTransport(respond)),
            ):
                result = await _provider_web_fetch_tool(self.config(provider, base_url='https://gateway.invalid/gateway/v1')).function('https://example.org/page')
            self.assertEqual(len(requests), 1)
            validate.assert_awaited_once_with('https://example.org/page', allow_local=False)
            self.assertEqual(result.return_value['content'], content)
            self.assertEqual(result.metadata['sources'][0]['url'], 'https://example.org/page')
            self.assertEqual(result.metadata['web_fetch']['provider'], provider)
            self.assertFalse(result.metadata['web_fetch']['truncated'])
            self.assertTrue(result.return_value['untrusted_external_content'])

    async def test_rejects_private_credentialed_or_invalid_url_before_supplier_call(self):
        for url in ['http://127.0.0.1/', 'http://169.254.169.254/latest/meta-data',
                    'http://[::1]/', 'file:///etc/passwd', 'https://name:secret@example.org/']:
            with patch('deep_agent.web.runtime.execute_deepseek_fetch', new=AsyncMock()) as fetch:
                with self.assertRaises(ModelRetry):
                    await _provider_web_fetch_tool(self.config()).function(url)
                fetch.assert_not_awaited()

    async def test_first_cloud_fetch_uses_proxy_safe_dns_without_prior_local_fetch(self):
        # A fresh process has not installed the local Fetch hooks yet. Fake-IP
        # DNS must not reject an otherwise public page before its cloud request.
        public = AsyncMock(return_value=["93.184.216.34"])
        original = AsyncMock(return_value=["198.18.0.27"])
        with patch.object(proxy_compat, "_PATCHED", False), \
             patch.object(proxy_compat, "_ORIGINAL_RESOLVE_HOSTNAME", original), \
             patch.object(proxy_compat, "_public_resolve_hostname", public), \
             patch.object(proxy_compat, "install_proxy_compatibility"), \
             patch("pydantic_ai._ssrf.resolve_hostname", new=proxy_compat._resolve_hostname), \
             patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:7897"}), \
             patch("deep_agent.web.runtime.execute_deepseek_fetch", new=AsyncMock(
                 return_value={"title": "public", "content": "body"},
             )) as fetch:
            result = await _provider_web_fetch_tool(self.config()).function("https://example.org/page")
        self.assertEqual(result.return_value["content"], "body")
        public.assert_awaited_once_with("example.org")
        original.assert_not_awaited()
        fetch.assert_awaited_once()

    async def test_proxy_safe_dns_still_rejects_private_origin(self):
        with patch.object(proxy_compat, "_PATCHED", False), \
             patch.object(proxy_compat, "_public_resolve_hostname", new=AsyncMock(
                 return_value=["10.0.0.1"],
             )), \
             patch.object(proxy_compat, "install_proxy_compatibility"), \
             patch("pydantic_ai._ssrf.resolve_hostname", new=proxy_compat._resolve_hostname), \
             patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:7897"}), \
             patch("deep_agent.web.runtime.execute_deepseek_fetch", new=AsyncMock()) as fetch:
            with self.assertRaises(ModelRetry):
                await _provider_web_fetch_tool(self.config()).function("https://example.org/page")
        fetch.assert_not_awaited()

    async def test_total_deadline_cancels_supplier_without_local_fallback(self):
        cancelled = asyncio.Event()

        async def slow_supplier(**kwargs):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

        with patch("deep_agent.web.runtime._WEB_FETCH_TOTAL_TIMEOUT_SECONDS", 0.02), \
             patch("deep_agent.web.runtime.validate_and_resolve_url", new=AsyncMock()), \
             patch("deep_agent.web.runtime.execute_deepseek_fetch", new=AsyncMock(
                 side_effect=slow_supplier,
             )) as fetch, \
             patch("deep_agent.web.runtime._web_fetch_tool") as local:
            with self.assertRaises(ToolFailed):
                await _provider_web_fetch_tool(self.config()).function("https://example.org/page")
        self.assertTrue(cancelled.is_set())
        self.assertEqual(fetch.await_count, 1)
        local.assert_not_called()

    async def test_error_is_not_local_fallback_and_credentials_are_redacted(self):
        error = httpx2.HTTPStatusError('fixture-secret', request=httpx2.Request('POST','https://vendor.invalid'),
                                     response=httpx2.Response(403, json={'error': {'message':'fixture-secret rejected','type':'security_risk'}}))
        with patch('deep_agent.web.runtime.validate_and_resolve_url', new=AsyncMock()), patch(
            'deep_agent.web.runtime.execute_deepseek_fetch', new=AsyncMock(side_effect=error)) as fetch, patch(
            'deep_agent.web.runtime._web_fetch_tool') as local:
            with self.assertRaises(ToolFailed) as ctx:
                await _provider_web_fetch_tool(self.config()).function('https://example.org/page')
        self.assertIn('403', str(ctx.exception))
        self.assertNotIn('fixture-secret', str(ctx.exception))
        self.assertEqual(fetch.await_count, 1)
        local.assert_not_called()

    async def test_retry_transient_only_and_cancellation_propagates(self):
        error = httpx2.HTTPStatusError('busy', request=httpx2.Request('POST','https://vendor.invalid'),
                                     response=httpx2.Response(429))
        with patch('deep_agent.web.runtime.validate_and_resolve_url', new=AsyncMock()), patch(
            'deep_agent.web.runtime.execute_deepseek_fetch', new=AsyncMock(side_effect=[error, {'title':'ok','content':'body'}])) as fetch:
            result = await _provider_web_fetch_tool(self.config()).function('https://example.org/page')
        self.assertEqual(fetch.await_count, 2)
        self.assertTrue(result.metadata['web_fetch']['retried'])
        with patch('deep_agent.web.runtime.validate_and_resolve_url', new=AsyncMock()), patch(
            'deep_agent.web.runtime.execute_deepseek_fetch', new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await _provider_web_fetch_tool(self.config()).function('https://example.org/page')

    async def test_empty_bad_and_redirect_responses_fail_without_fabricating_text(self):
        original = httpx2.AsyncClient
        for status, payload in [(200, {}), (200, {'title':'t','content':''}), (200, {'title':'t','summary':'answer'}), (302,{})]:
            with patch('deep_agent.web.runtime.validate_and_resolve_url', new=AsyncMock()), patch(
                'deep_agent.web.provider_api.httpx2.AsyncClient',
                side_effect=lambda **kw: original(**kw, transport=httpx2.MockTransport(
                    lambda req: httpx2.Response(status, json=payload, headers={'location':'https://other.invalid'}))),
            ):
                with self.assertRaises(ToolFailed):
                    await _provider_web_fetch_tool(self.config()).function('https://example.org/page')

    async def test_same_agent_tool_names_and_both_supplier_search_then_fetch(self):
        for provider in ('deepseek', 'kimi'):
            calls=[]
            def model(messages, info):
                self.assertIn('web_fetch',[tool.name for tool in info.function_tools])
                calls.append(True)
                if len(calls)==1:
                    return ModelResponse(parts=[ToolCallPart('web_search', {'query':'docs'}, 'search')])
                if len(calls)==2:
                    return ModelResponse(parts=[ToolCallPart('web_fetch', {'url':'https://example.org/page'}, 'fetch')])
                results=[p for m in messages for p in m.parts if isinstance(p,ToolReturnPart)]
                self.assertTrue(any('source body' in str(p.content) for p in results))
                return ModelResponse(parts=[TextPart('[来源](https://example.org/page)')])
            from pydantic_ai.messages import ToolReturn
            with patch('deep_agent.web.runtime.execute_provider_search', new=AsyncMock(return_value=ToolReturn(return_value={'url':'https://example.org/page'}))), patch(
                'deep_agent.web.runtime.validate_and_resolve_url', new=AsyncMock()), patch(
                f'deep_agent.web.runtime.execute_{provider}_fetch', new=AsyncMock(return_value={'title':'Docs','content':'source body'})):
                result=await Agent(FunctionModel(model), toolsets=[build_web_runtime(self.config(provider)).toolset]).run('search then fetch')
                self.assertIn('来源',result.output)
            self.assertEqual(len(calls),3)

    def test_gateway_and_official_paths(self):
        for base, expected in [(None,'https://api.moonshot.cn/v1/tools/fetch'),
                               ('https://gateway.example/prefix/v1','https://gateway.example/prefix/v1/tools/fetch'),
                               ('https://gateway.example/prefix/v1/tools/search','https://gateway.example/prefix/v1/tools/fetch')]:
            self.assertEqual(tool_endpoint(base, default='https://api.moonshot.cn/v1', tool='fetch'),expected)
