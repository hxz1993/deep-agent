"""Search probes share the tool retrieval route; failures remain nonterminal tool results."""
import unittest
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent, ToolFailed
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart, TextPart
from deep_agent.agent.events import map_part
from deep_agent.web.runtime import build_web_runtime, probe_web_search_config
from deep_agent.web.settings import WebSearchRuntimeConfig, resolve_web_search_config


class SearchProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_uses_same_retrieval_without_model(self):
        for provider in ('deepseek', 'kimi', 'duckduckgo'):
            config = WebSearchRuntimeConfig(mode='provider', provider=provider, api_key='fixture')
            with patch('deep_agent.web.runtime.execute_provider_search', new=AsyncMock()) as search:
                await probe_web_search_config(config)
            search.assert_awaited_once()
            self.assertIs(search.call_args.args[0], config)

    async def test_missing_config_result_is_paired_and_model_can_continue(self):
        for value in (None, {}, {'mode': 'native'}):
            calls = []
            def model(messages, info):
                calls.append(True)
                if len(calls) == 1:
                    self.assertIn('web_search', [t.name for t in info.function_tools])
                    return ModelResponse(parts=[ToolCallPart('web_search', {'query': 'news'}, tool_call_id='search-1')])
                part = next(p for m in messages for p in m.parts if isinstance(p, ToolReturnPart))
                self.assertEqual(part.outcome, 'failed')
                self.assertIn('未配置具体', part.content)
                event = map_part(part)
                self.assertEqual(event['type'], 'tool_result')
                self.assertEqual(event['outcome'], 'failed')
                self.assertEqual(event['tool_call_id'], 'search-1')
                return ModelResponse(parts=[TextPart('网络搜索工具调用失败：未配置供应商。')])
            runtime = build_web_runtime(resolve_web_search_config(value))
            result = await Agent(FunctionModel(model), toolsets=[runtime.toolset]).run('search news')
            self.assertIn('未配置供应商', result.output)
            self.assertEqual(len(calls), 2)

    async def test_probe_failure_preserves_reason(self):
        with patch('deep_agent.web.runtime.execute_provider_search', new=AsyncMock(side_effect=ToolFailed('upstream 401'))):
            with self.assertRaisesRegex(ToolFailed, 'upstream 401'):
                await probe_web_search_config(WebSearchRuntimeConfig(mode='provider', provider='kimi', api_key='fixture'))
