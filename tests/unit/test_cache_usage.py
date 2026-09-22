from __future__ import annotations

import copy
from contextlib import asynccontextmanager
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart
from pydantic_ai.usage import RequestUsage

from deep_agent.agent.cache_usage import (
    CACHE_USAGE_METADATA, cache_message_summary, stamp_cache_usage, summarize_cache_usage,
)
from deep_agent.agent.diagnostics import RuntimeDiagnostics


class CacheUsageTests(unittest.TestCase):
    def response(self, **usage):
        return ModelResponse(parts=[TextPart('answer')], usage=RequestUsage(**usage))

    def test_real_sdk_provider_extraction_and_inclusive_anthropic_denominator(self):
        fixtures = [
            ('deepseek', 'https://api.deepseek.com', 'chat',
             {'prompt_tokens': 10000, 'completion_tokens': 500,
              'prompt_cache_hit_tokens': 8000, 'prompt_cache_miss_tokens': 2000},
             {'prompt_cache_hit_tokens': 8000, 'prompt_cache_miss_tokens': 2000}, 10000, 8000, None),
            ('openai', 'https://api.openai.com/v1', 'chat',
             {'prompt_tokens': 6000, 'completion_tokens': 500, 'prompt_tokens_details': {'cached_tokens': 3000}},
             {}, 6000, 3000, None),
            ('anthropic', 'https://api.anthropic.com', 'default',
             {'input_tokens': 1000, 'output_tokens': 50, 'cache_read_input_tokens': 8000,
              'cache_creation_input_tokens': 1000}, {}, 10000, 8000, 1000),
        ]
        for provider, url, flavor, wire, details, expected_input, expected_read, expected_write in fixtures:
            with self.subTest(provider=provider):
                usage = RequestUsage.extract({'usage': wire}, provider=provider, provider_url=url,
                    provider_fallback=provider, api_flavor=flavor, details=details)
                original = copy.deepcopy(usage.__dict__)
                response = ModelResponse(parts=[TextPart('done')], provider_name=provider, usage=usage)
                stamp_cache_usage(response)
                summary = cache_message_summary(response)
                self.assertEqual(summary['input_tokens'], expected_input)
                self.assertEqual(summary['cache_read_tokens'], expected_read)
                self.assertEqual(summary['cache_write_tokens'], expected_write)
                self.assertEqual(summary['hit_ratio'], expected_read / expected_input)
                self.assertEqual(usage.__dict__, original)

    def test_explicit_zero_and_missing_survive_serialization_and_reload(self):
        responses = []
        for details in ({'cached_tokens': 0}, None):
            wire = {'prompt_tokens': 20, 'completion_tokens': 2}
            if details is not None:
                wire['prompt_tokens_details'] = details
            usage = RequestUsage.extract({'usage': wire}, provider='openai',
                provider_url='https://api.openai.com/v1', provider_fallback='openai', api_flavor='chat')
            response = ModelResponse(parts=[TextPart('done')], usage=usage, metadata={'other': 'retained'})
            stamp_cache_usage(response)
            responses.append(response)
        self.assertEqual(responses[0].metadata[CACHE_USAGE_METADATA]['read_reported'], True)
        self.assertEqual(responses[1].metadata[CACHE_USAGE_METADATA]['read_reported'], False)
        wire = ModelMessagesTypeAdapter.dump_python(responses, mode='json')
        loaded = ModelMessagesTypeAdapter.validate_python(wire)
        for values in (responses, wire, loaded):
            self.assertEqual(cache_message_summary(values[0])['hit_ratio'], 0)
            self.assertIsNone(cache_message_summary(values[1])['cache_read_tokens'])
            self.assertFalse(cache_message_summary(values[0])['write_complete'])
        self.assertEqual(loaded[0].metadata['other'], 'retained')

    def test_old_serialized_defaults_and_deserialized_sdk_defaults_are_unknown(self):
        response = self.response(input_tokens=20, output_tokens=2)
        wire = ModelMessagesTypeAdapter.dump_python([response], mode='json')
        self.assertEqual(wire[0]['usage']['cache_read_tokens'], 0)
        for message in (wire[0], ModelMessagesTypeAdapter.validate_python(wire)[0]):
            self.assertIsNone(cache_message_summary(message)['cache_read_tokens'])
            self.assertIsNone(cache_message_summary(message)['hit_ratio'])

    def test_known_alias_zero_and_unknown_provider_details(self):
        for provider, details, expected in (
            ('deepseek', {'prompt_cache_hit_tokens': 0}, 0),
            ('anthropic', {'cache_read_input_tokens': 0}, 0),
            ('custom', {'prompt_cache_hit_tokens': 0}, None),
            ('deepseek', {'cache_hit_percentage': 99}, None),
        ):
            response = self.response(input_tokens=100, details=details)
            response.provider_name = provider
            self.assertEqual(cache_message_summary(response)['cache_read_tokens'], expected)

    def test_weighted_aggregate_and_partial_fields_are_independent(self):
        first = self.response(input_tokens=10000, cache_read_tokens=8000)
        second = self.response(input_tokens=6000, cache_read_tokens=3000, cache_write_tokens=0)
        for response in (first, second):
            stamp_cache_usage(response)
        summary = summarize_cache_usage([first, second])
        self.assertEqual(summary['hit_ratio'], .6875)
        self.assertEqual(summary['cache_read_tokens'], 11000)
        self.assertFalse(summary['write_complete'])
        self.assertEqual(summary['cache_write_tokens'], 0)
        summary = summarize_cache_usage([first, second, self.response(input_tokens=20)])
        self.assertEqual(summary['cache_read_tokens'], 11000)
        self.assertFalse(summary['read_complete'])
        self.assertIsNone(summary['hit_ratio'])

    def test_invalid_numbers_and_inconsistent_ratios_are_not_repaired(self):
        for value in (-1, True, 2.5, '10'):
            response = self.response(input_tokens=100, cache_read_tokens=value,
                details={'prompt_cache_hit_tokens': 10})
            response.provider_name = 'deepseek'
            stamp_cache_usage(response)
            self.assertIsNone(cache_message_summary(response)['cache_read_tokens'])
        excessive = self.response(input_tokens=10, cache_read_tokens=20)
        other = self.response(input_tokens=1000, cache_read_tokens=100)
        self.assertIsNone(summarize_cache_usage([excessive, other])['hit_ratio'])
        self.assertIsNone(cache_message_summary(self.response(cache_read_tokens=20))['hit_ratio'])

    def test_replayed_response_identity_deduplicates_but_provider_identity_is_scoped(self):
        response = self.response(input_tokens=100, cache_read_tokens=20)
        response.run_id = 'run'
        response.provider_response_id = 'response'
        response.provider_name = 'provider-a'
        other = copy.deepcopy(response)
        other.provider_name = 'provider-b'
        summary = summarize_cache_usage([response, copy.deepcopy(response), other])
        self.assertEqual(summary['response_count'], 2)
        self.assertEqual(summary['input_tokens'], 200)


class CacheUsageRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_stream_and_nonstream_stamp_zero_before_incremental_event_save(self):
        from pydantic_ai import Agent
        from pydantic_ai.messages import ToolCallPart
        from pydantic_ai.models.function import DeltaToolCall, FunctionModel
        from pydantic_ai.usage import UsageLimits
        from deep_agent.agent.deps import AgentDeps
        from deep_agent.agent.event_persister import EventPersister
        from deep_agent.agent.runner import AgentRunner
        from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent

        class ZeroCacheFunctionModel(FunctionModel):
            # Simulate a provider that reports zero reads and omits cache writes;
            # keep real SDK streaming, capability and persistence paths intact.
            @asynccontextmanager
            async def request_stream(self, *args, **kwargs):
                async with super().request_stream(*args, **kwargs) as stream:
                    stream._usage.cache_read_tokens = 0
                    yield stream

        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                stores, engine, _ = await make_sqlite_stores()
                agent_id = await seed_default_agent(stores)
                session_id = f"cache-boundary-{streaming}"
                requests, saved_before_tool = [], []

                async def lookup():
                    # Independent SQLite read while the tool is running proves
                    # provenance preceded the first response Event, not only a
                    # terminal history rewrite.
                    saved = await stores.coordinator.get_raw_history(session_id, agent_id=agent_id)
                    response = next(message for message in saved if message.kind == "response")
                    saved_before_tool.append(response.metadata[CACHE_USAGE_METADATA])
                    self.assertTrue(cache_message_summary(response)["read_complete"])
                    self.assertEqual(cache_message_summary(response)["hit_ratio"], 0)
                    self.assertFalse(cache_message_summary(response)["write_complete"])
                    return "found"

                def respond(messages, info):
                    requests.append(True)
                    part = ToolCallPart("lookup", {}, "call") if len(requests) == 1 else TextPart("done")
                    return ModelResponse(parts=[part], usage=RequestUsage(
                        input_tokens=100, output_tokens=10, cache_read_tokens=0))

                async def stream(messages, info):
                    requests.append(True)
                    if len(requests) == 1:
                        yield {0: DeltaToolCall(name="lookup", json_args="{}", tool_call_id="call")}
                    else:
                        yield "done"

                model = ZeroCacheFunctionModel(respond, stream_function=stream)
                runner = AgentRunner(Agent(model, deps_type=AgentDeps, tools=[lookup],
                    capabilities=[RuntimeDiagnostics()]), stores.coordinator, UsageLimits(request_limit=5))
                persister = EventPersister(stores.coordinator)
                await persister.start()
                deps = AgentDeps(session_id=session_id, agent_id=agent_id, user="cache-fixture")
                try:
                    with patch.object(AgentRunner, "_generate_model_title", return_value=None), \
                            patch("deep_agent.agent.diagnostics.log_event"):
                        if streaming:
                            events = [event async for event in runner.run_stream(session_id, "lookup", deps,
                                persister=persister)]
                            self.assertEqual(events[-1]["type"], "final")
                        else:
                            result = await runner.run(session_id, "lookup", deps)
                            self.assertEqual(result["status"], "completed")
                    self.assertEqual(len(saved_before_tool), 1)
                    history = await stores.coordinator.get_raw_history(session_id, agent_id=agent_id)
                    responses = [message for message in history if message.kind == "response"]
                    self.assertEqual(len(responses), 2)
                    self.assertTrue(all(message.metadata[CACHE_USAGE_METADATA] == {
                        "version": 1, "read_reported": True, "write_reported": False,
                    } for message in responses))
                    self.assertEqual(summarize_cache_usage(responses)["hit_ratio"], 0)
                finally:
                    await persister.aclose()
                    await engine.dispose()

    async def test_fresh_response_is_stamped_and_each_log_field_has_own_availability(self):
        response = ModelResponse(parts=[TextPart('done')],
            usage=RequestUsage(input_tokens=20, output_tokens=2, cache_read_tokens=0))
        context = SimpleNamespace(deps=SimpleNamespace(request_headers=None))
        with patch('deep_agent.agent.diagnostics.log_event') as log:
            actual = await RuntimeDiagnostics().wrap_model_request(context,
                request_context=SimpleNamespace(model=None), handler=AsyncMock(return_value=response))
        self.assertIs(actual, response)
        self.assertTrue(actual.metadata[CACHE_USAGE_METADATA]['read_reported'])
        completed = next(call.kwargs for call in log.call_args_list if call.args[0] == 'model_request_completed')
        self.assertEqual(completed['cache_read_tokens'], 0)
        self.assertIsNone(completed['cache_write_tokens'])
