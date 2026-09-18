"""Search probes must stop at a real successful tool result and close the stream."""
import asyncio
import json
import unittest

import httpx2
from pydantic_ai import Agent, ToolFailed
from pydantic_ai.capabilities import WebSearch
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.deepseek import DeepSeekProvider

from deep_agent.web.runtime import _probe_search_agent


class SearchProbeTests(unittest.IsolatedAsyncioTestCase):
    async def run_native(self, status):
        closed = []
        class Stream(httpx2.AsyncByteStream):
            async def __aiter__(self):
                item = {"type": "web_search_call", "id": "ws_1", "status": "in_progress"}
                for i, (kind, value) in enumerate((('added', item), ('done', {**item, "status": status}))):
                    event = {"type": f"response.output_item.{kind}", "output_index": 0,
                             "sequence_number": i, "item": value}
                    yield ('data: ' + json.dumps(event) + '\n\n').encode()
                # A final answer is deliberately unavailable. Success must not wait for it.
                await asyncio.sleep(30)
            async def aclose(self):
                closed.append(True)

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, headers={'content-type': 'text/event-stream'}, stream=Stream())
        )) as http:
            model = OpenAIResponsesModel('deepseek-v4-pro', provider=DeepSeekProvider(api_key='fixture', http_client=http))
            try:
                async with asyncio.timeout(2):
                    await _probe_search_agent(Agent(model, capabilities=[WebSearch(native=True, local=False)]))
            finally:
                self.assertTrue(closed, 'HTTP response stream must be closed on early exit')

    async def test_native_completed_search_returns_before_final_answer(self):
        await self.run_native('completed')

    async def test_native_failed_search_is_not_success(self):
        with self.assertRaises(ToolFailed):
            await self.run_native('failed')

    async def test_plain_text_is_not_search_success(self):
        from pydantic_ai.models.function import FunctionModel
        async def text_only(messages, info):
            yield 'OK'
        with self.assertRaisesRegex(ToolFailed, '未返回搜索执行成功的证据'):
            await _probe_search_agent(Agent(FunctionModel(stream_function=text_only)))

    async def test_function_search_stops_without_waiting_for_followup(self):
        from pydantic_ai.models.function import FunctionModel, DeltaToolCall
        calls = []
        completed = []

        async def stream(messages, info):
            calls.append(True)
            if len(calls) == 1:
                yield {0: DeltaToolCall(name="web_search", json_args='{"query":"Python"}')}
            else:
                await asyncio.sleep(30)
                yield 'Final answer'

        async def web_search(query: str) -> str:
            completed.append(query)
            return "https://python.org"

        async with asyncio.timeout(2):
            await _probe_search_agent(Agent(FunctionModel(stream_function=stream), tools=[web_search]))
        self.assertEqual(completed, ["Python"])

    async def test_fixed_kimi_stops_before_second_model_request(self):
        from tests.unit.test_kimi_web_search import KimiFormulaTests
        await KimiFormulaTests().run_loop(False, probe_only=True)

    async def test_google_grounding_metadata_is_search_evidence(self):
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        for searched in (True, False):
            with self.subTest(searched=searched):
                candidate = {'content': {'role': 'model', 'parts': [{'text': 'https://python.org'}]},
                             'finishReason': 'STOP'}
                if searched:
                    candidate['groundingMetadata'] = {
                        'webSearchQueries': ['Python latest release'],
                        'groundingChunks': [{'web': {'uri': 'https://python.org', 'title': 'Python'}}],
                    }
                def respond(request):
                    self.assertIn(':generateContent', request.url.path)
                    self.assertNotIn('streamGenerateContent', request.url.path)
                    return httpx2.Response(200, json={'candidates': [candidate], 'modelVersion': 'gemini-2.5-flash'})
                async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
                    model = GoogleModel('gemini-2.5-flash', provider=GoogleProvider(api_key='fixture', http_client=http))
                    agent = Agent(model, capabilities=[WebSearch(native=True, local=False)])
                    if searched:
                        await _probe_search_agent(agent, provider_type='google')
                    else:
                        with self.assertRaises(ToolFailed):
                            await _probe_search_agent(agent, provider_type='google')

    async def test_openrouter_server_search_usage_is_required(self):
        from pydantic_ai.models.openrouter import OpenRouterModel
        from pydantic_ai.providers.openrouter import OpenRouterProvider

        for count in (1, 0):
            with self.subTest(count=count):
                def respond(request):
                    self.assertFalse(json.loads(request.content).get('stream', False))
                    return httpx2.Response(200, json={
                        'id': 'fixture', 'object': 'chat.completion', 'created': 1,
                        'model': 'openai/gpt-4.1', 'provider': 'OpenAI',
                        'choices': [{'index': 0, 'finish_reason': 'stop',
                                     'message': {'role': 'assistant', 'content': 'https://python.org'}}],
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15,
                                  'server_tool_use_details': {'web_search_requests': count}},
                    })
                async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
                    model = OpenRouterModel('openai/gpt-4.1', provider=OpenRouterProvider(api_key='fixture', http_client=http))
                    agent = Agent(model, capabilities=[WebSearch(native=True, local=False)])
                    if count:
                        await _probe_search_agent(agent, provider_type='openrouter')
                    else:
                        with self.assertRaises(ToolFailed):
                            await _probe_search_agent(agent, provider_type='openrouter')
