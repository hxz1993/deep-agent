from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RequestUsage, RunUsage

from deep_agent.agent.model_api_diagnostics import observe_model_api, observed_model, _scope
from deep_agent.agent.title_generator import _title_model_settings
from deep_agent.agent.context.compaction import _summary_model_settings
from deep_agent.agent.spec import AgentSpec
from deep_agent.api.v1.deps import _build_runner_from_spec
from deep_agent.models.context_window import ContextWindowModel


class ContextWindowModelTests(unittest.IsolatedAsyncioTestCase):
    def test_configured_window_drives_sdk_ratio_without_mutating_profile(self):
        raw = TestModel(profile=ModelProfile(context_window=100_000))
        model = ContextWindowModel(raw, 20_000)
        response = ModelResponse([TextPart('done')], usage=RequestUsage(input_tokens=4_000, output_tokens=1_000))
        ctx = RunContext(deps=None, model=model, usage=RunUsage(), messages=[response])
        self.assertEqual(ctx.context_window_used, 0.25)
        self.assertEqual(raw.context_window, 100_000)
        self.assertIs(model.profile, raw.profile)
        self.assertEqual(model.model_id, raw.model_id)
        self.assertEqual(model.model_name, raw.model_name)
        ctx.messages = []
        self.assertIsNone(ctx.context_window_used)

    async def test_request_and_stream_preserve_real_sdk_behavior(self):
        raw = TestModel(call_tools=[], custom_output_text='fixture answer')
        model = ContextWindowModel(raw, 32_000)
        agent = Agent(model)
        result = await agent.run('hello')
        self.assertEqual(result.output, 'fixture answer')
        async with agent.run_stream('hello') as streamed:
            self.assertEqual(await streamed.get_output(), 'fixture answer')
        self.assertEqual(model.context_window, 32_000)

    async def test_runtime_assembly_applies_configured_capacity(self):
        spec = AgentSpec(name='test', model='test', instructions='helpful', context_window_tokens=32_000)
        ctx = SimpleNamespace(stores=MagicMock(), registry=MagicMock(), store=MagicMock(), skill_runtime=MagicMock())
        agent = Agent(TestModel(call_tools=[], custom_output_text='done'))
        with (
            patch('deep_agent.api.v1.deps.load_mcp_rows', new=AsyncMock(return_value=[])),
            patch('deep_agent.api.v1.deps.prepare_ui_tool_packages_for_spec', new=AsyncMock(return_value=([], {}))),
            patch('deep_agent.api.v1.deps.resolve_mcp_toolsets', new=AsyncMock(return_value=[])),
            patch('deep_agent.api.v1.deps.build_mcp_resource_toolset', return_value=None),
            patch('deep_agent.api.v1.deps.augment_instructions_with_capabilities', return_value='helpful'),
            patch('deep_agent.api.v1.deps.build_agent', return_value=agent),
        ):
            runner = await _build_runner_from_spec(spec, ctx)
        self.assertIs(runner.agent, agent)
        self.assertEqual(runner.agent.model.context_window, 32_000)

    async def test_observation_and_auxiliary_settings_survive_wrapping(self):
        async with httpx2.AsyncClient() as client:
            raw = OpenAIChatModel('deepseek-fixture', provider=OpenAIProvider(api_key='fixture', http_client=client))
            model = ContextWindowModel(raw, 32_000)
            observed = observed_model(model, {'session_id': 'fixture'}, {})
            self.assertIsNot(observed, model)
            self.assertEqual(observed.context_window, 32_000)
            self.assertEqual(observed.model_id, raw.model_id)
            self.assertIs(observed.provider, raw.provider)
            self.assertEqual(observed.base_url, raw.base_url)
            with observe_model_api(observed, {'session_id': 'fixture'}, {}):
                self.assertIsNotNone(_scope.get())
                self.assertTrue(client.event_hooks['request'])
            self.assertIsNone(_scope.get())
            self.assertEqual(_title_model_settings(observed, 'deepseek')['extra_body'], {'thinking': {'type': 'disabled'}})
            # The summary path already unwraps providers, retaining its settings.
            self.assertEqual(_summary_model_settings(observed, 512), _summary_model_settings(raw, 512))


if __name__ == '__main__':
    unittest.main()
