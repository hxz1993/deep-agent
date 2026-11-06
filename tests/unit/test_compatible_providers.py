"""Real SDK wire contracts for the added compatible providers, without network."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import httpx2
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessagesTypeAdapter, ThinkingPart
from pydantic_ai.messages import BinaryContent
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic import BaseModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from deep_agent.models.compatible_providers import MiniMaxProvider, StepFunProvider
from deep_agent.models.provider_registry import ProviderRuntimeConfig, resolve_provider_model


def _config(code: str, *, custom: str | None = None) -> ProviderRuntimeConfig:
    return ProviderRuntimeConfig(
        id=f"fixture-{code}", name=code, provider_type=code,
        use_default_base_url=custom is None, base_url=custom or "",
        api_key="fixture-only-key",
    )


def _sse(events: list[dict], *, named: bool = False) -> httpx2.Response:
    body = "".join(
        (f"event: {event['type']}\n" if named else "")
        + "data: " + json.dumps(event) + "\n\n" for event in events
    )
    if not named:
        body += "data: [DONE]\n\n"
    return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})


def _anthropic_reply(*, tools: bool, stream: bool) -> httpx2.Response:
    blocks = ([
        {"type": "thinking", "thinking": "Add the two values.", "signature": "fixture-signature"},
        {"type": "tool_use", "id": "call-add", "name": "add", "input": {"left": 97, "right": 163}},
    ] if tools else [{"type": "text", "text": "260"}])
    message = {
        "id": "msg-fixture", "type": "message", "role": "assistant", "model": "MiniMax-M2.7",
        "content": blocks, "stop_reason": "tool_use" if tools else "end_turn",
        "stop_sequence": None, "usage": {"input_tokens": 12, "output_tokens": 4},
    }
    if not stream:
        return httpx2.Response(200, json=message)
    events = [{"type": "message_start", "message": {**message, "content": [], "stop_reason": None}}]
    for index, block in enumerate(blocks):
        kind = block["type"]
        if kind == "thinking":
            start = {"type": "thinking", "thinking": "", "signature": ""}
            deltas = [
                {"type": "thinking_delta", "thinking": block["thinking"]},
                {"type": "signature_delta", "signature": block["signature"]},
            ]
        elif kind == "tool_use":
            start = {**block, "input": {}}
            deltas = [{"type": "input_json_delta", "partial_json": json.dumps(block["input"])}]
        else:
            start = {"type": "text", "text": ""}
            deltas = [{"type": "text_delta", "text": block["text"]}]
        events.append({"type": "content_block_start", "index": index, "content_block": start})
        events.extend({"type": "content_block_delta", "index": index, "delta": delta} for delta in deltas)
        events.append({"type": "content_block_stop", "index": index})
    events.extend([
        {"type": "message_delta", "delta": {"stop_reason": message["stop_reason"], "stop_sequence": None},
         "usage": {"output_tokens": 4}},
        {"type": "message_stop"},
    ])
    return _sse(events, named=True)


def _openai_reply(*, tools: bool, stream: bool) -> httpx2.Response:
    message = ({
        "role": "assistant", "content": None, "reasoning_content": "Add the two values.",
        "tool_calls": [{"id": "call-add", "type": "function", "function": {
            "name": "add", "arguments": '{"left":97,"right":163}',
        }}],
    } if tools else {"role": "assistant", "content": "260"})
    response = {
        "id": "chat-fixture", "object": "chat.completion", "created": 1,
        "model": "step-3.5-flash", "choices": [{"index": 0, "message": message,
        "finish_reason": "tool_calls" if tools else "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
    }
    if not stream:
        return httpx2.Response(200, json=response)
    delta = dict(message)
    if tools:
        delta["tool_calls"] = [{"index": 0, **item} for item in message["tool_calls"]]
    events = [{
        "id": response["id"], "object": "chat.completion.chunk", "created": 1,
        "model": response["model"], "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14},
    }, {
        "id": response["id"], "object": "chat.completion.chunk", "created": 1,
        "model": response["model"], "choices": [{"index": 0, "delta": {},
        "finish_reason": "tool_calls" if tools else "stop"}], "usage": response["usage"],
    }]
    events.append(dict(events[-1]))  # StepFun may repeat its final cumulative totals.
    events.append({**events[-1], "usage": None, "choices": []})
    return _sse(events)


def _responses_reply(*, tools: bool, stream: bool, structured: bool = False) -> httpx2.Response:
    reasoning = {"id": "rs-fixture", "type": "reasoning", "summary": [
        {"type": "summary_text", "text": "Use the addition tool."},
    ]}
    output = ({"id": "fc-fixture", "type": "function_call", "call_id": "call-add",
               "name": "add", "arguments": '{"left":97,"right":163}', "status": "completed"}
              if tools else {"id": "msg-fixture", "type": "message", "role": "assistant", "status": "completed",
                             "content": [{"type": "output_text", "text": '{"value":260}' if structured else "260", "annotations": []}]})
    response = {"id": "resp-fixture", "object": "response", "created_at": 1, "status": "completed",
                "model": "gpt-6-luna", "output": [reasoning, output], "error": None,
                "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30,
                          "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 4}}}
    if not stream:
        return httpx2.Response(200, json=response)
    events = [
        {"type": "response.created", "response": {**response, "output": [], "status": "in_progress"}},
        {"type": "response.output_item.added", "output_index": 0, "item": {**reasoning, "summary": []}},
        {"type": "response.reasoning_summary_text.delta", "item_id": reasoning['id'], "output_index": 0,
         "summary_index": 0, "delta": "Use the addition tool."},
        {"type": "response.output_item.added", "output_index": 1,
         "item": {**output, "arguments": ""} if tools else {**output, "content": []}},
    ]
    if tools:
        events.append({"type": "response.function_call_arguments.delta", "item_id": output['id'],
                       "output_index": 1, "delta": output['arguments']})
    else:
        events.append({"type": "response.output_text.delta", "item_id": output['id'], "output_index": 1,
                       "content_index": 0, "delta": output['content'][0]['text'], "logprobs": []})
    events.extend([{"type": "response.output_item.done", "output_index": 1, "item": output},
                   {"type": "response.completed", "response": response}])
    for index, event in enumerate(events):
        event['sequence_number'] = index
    return _sse(events, named=True)


class CompatibleProviderWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_stepfun_cumulative_usage_replaces_duplicates_and_preserves_missing_chunks(self):
        for video in (False, True):
            with self.subTest(video=video):
                def respond(request):
                    body = json.loads(request.content)
                    self.assertEqual(body['stream_options'], {'include_usage': True, 'continuous_usage_stats': True})
                    chunk = {'id': 'step-usage', 'object': 'chat.completion.chunk', 'created': 1,
                             'model': 'step-5-preview', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': None}]}
                    events = []
                    for output in (1, None, 2, 2, 'empty', None):
                        event = {**chunk, 'choices': [{'index': 0, 'delta': {'content': 'ok' if not events else ''}, 'finish_reason': None}]}
                        if output == 'empty':
                            event['usage'] = {}
                        elif output is not None:
                            event['usage'] = {'prompt_tokens': 20, 'completion_tokens': output, 'total_tokens': 20 + output}
                        events.append(event)
                    events.append({**chunk, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})
                    return _sse(events)
                async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
                    with patch('deep_agent.models.provider_registry.StepFunProvider', side_effect=lambda **kw: StepFunProvider(http_client=client, **kw)):
                        model = resolve_provider_model(model_name='step-5-preview', provider=_config('stepfun'), input_modalities=['text', 'video'] if video else ['text'])
                    async with Agent(model).run_stream('reply') as result:
                        self.assertEqual(await result.get_output(), 'ok')
                        self.assertEqual(result.usage.input_tokens, 20)
                        self.assertEqual(result.usage.output_tokens, 2)

    async def test_explicit_responses_reasoning_tools_history_and_native_output(self):
        for provider_type in ('openai', 'openai_compatible'):
            for stream in (False, True):
                with self.subTest(provider=provider_type, stream=stream):
                    requests, executions = [], []
                    def respond(request):
                        body = json.loads(request.content)
                        requests.append(body)
                        self.assertEqual(request.url.path, '/v1/responses')
                        self.assertEqual(body['reasoning'].get('summary'), 'auto')
                        self.assertNotIn('effort', body['reasoning'])
                        return _responses_reply(tools=len(requests) == 1, stream=body.get('stream', False))
                    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
                        provider = OpenAIProvider(api_key='fixture-only', base_url='https://gateway.example/v1', http_client=client)
                        with patch('deep_agent.models.provider_registry._openai_style_provider', return_value=provider):
                            model = resolve_provider_model(model_name='gpt-6-luna', provider=_config(provider_type, custom='https://gateway.example/v1'), tags=['reasoning', 'openai_responses'])
                        agent = Agent(model)
                        @agent.tool_plain
                        def add(left: int, right: int) -> int:
                            executions.append((left, right))
                            return left + right
                        if stream:
                            async with agent.run_stream('add') as result:
                                self.assertEqual(await result.get_output(), '260')
                                history = result.all_messages()
                        else:
                            result = await agent.run('add')
                            self.assertEqual(result.output, '260')
                            history = result.all_messages()
                        restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(history))
                        self.assertEqual((await agent.run('repeat', message_history=restored)).output, '260')
                    self.assertEqual(executions, [(97, 163)])
                    self.assertTrue(any(isinstance(p, ThinkingPart) and p.content for m in restored for p in m.parts))
                    self.assertTrue(any(item.get('type') == 'function_call_output' and item.get('call_id') == 'call-add' for item in requests[1]['input']))

    async def test_responses_native_structured_output_and_image_wire(self):
        class Answer(BaseModel):
            value: int
        for stream in (False, True):
            with self.subTest(stream=stream):
                requests = []
                def respond(request):
                    body = json.loads(request.content)
                    requests.append(body)
                    return _responses_reply(tools=False, stream=body.get('stream', False), structured=True)
                async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
                    provider = OpenAIProvider(api_key='fixture-only', http_client=client)
                    with patch('deep_agent.models.provider_registry._openai_style_provider', return_value=provider):
                        model = resolve_provider_model(model_name='gpt-6-luna', provider=_config('openai'), tags=['reasoning', 'openai_responses'])
                    from pydantic_ai import NativeOutput
                    agent = Agent(model, output_type=NativeOutput(Answer))
                    prompt = ['return value', BinaryContent(data=b'fixture-image', media_type='image/png')]
                    if stream:
                        async with agent.run_stream(prompt) as result:
                            self.assertEqual((await result.get_output()).value, 260)
                    else:
                        self.assertEqual((await agent.run(prompt)).output.value, 260)
                self.assertEqual(requests[0]['text']['format']['type'], 'json_schema')
                self.assertTrue(requests[0]['text']['format']['strict'])
                self.assertTrue(any(item.get('type') == 'input_image' for message in requests[0]['input'] for item in message.get('content', []) if isinstance(item, dict)))

    async def _tool_round_trip(self, code: str, stream: bool) -> list:
        calls, executions = [], []

        def respond(request):
            body = json.loads(request.content)
            calls.append((request, body))
            return (_anthropic_reply if code == "minimax" else _openai_reply)(
                tools=len(calls) == 1, stream=body.get("stream", False),
            )

        provider_class = MiniMaxProvider if code == "minimax" else StepFunProvider
        provider_symbol = "MiniMaxProvider" if code == "minimax" else "StepFunProvider"
        model_name = "MiniMax-M2.7" if code == "minimax" else "step-3.5-flash"
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
            with patch(f"deep_agent.models.provider_registry.{provider_symbol}",
                       side_effect=lambda **kw: provider_class(http_client=client, **kw)):
                model = resolve_provider_model(model_name=model_name, provider=_config(code))
            agent = Agent(model, model_settings={"max_tokens": 128})

            @agent.tool_plain(strict=True)
            def add(left: int, right: int) -> int:
                """Add two integer values."""
                executions.append((left, right))
                return left + right

            if stream:
                async with agent.run_stream("Add 97 and 163 using the tool") as result:
                    self.assertEqual(await result.get_output(), "260")
                    history = result.all_messages()
                    if code == 'stepfun':
                        self.assertEqual(result.usage.input_tokens, 24)
                        self.assertEqual(result.usage.output_tokens, 8)
            else:
                result = await agent.run("Add 97 and 163 using the tool")
                self.assertEqual(result.output, "260")
                history = result.all_messages()
            # Exercise the same SDK history serialization that storage/reopen uses.
            restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(history))
            next_turn = await agent.run("Repeat the previous result", message_history=restored)
            self.assertEqual(next_turn.output, "260")

        self.assertEqual(executions, [(97, 163)])
        self.assertEqual(len(calls), 3)
        thinking = [part for message in restored for part in message.parts if isinstance(part, ThinkingPart)]
        self.assertEqual(len(thinking), 1)
        self.assertEqual(thinking[0].content, "Add the two values.")
        for request, body in calls:
            self.assertEqual(body["max_tokens"], 128)
            self.assertNotIn("max_completion_tokens", body)
            if code == "minimax":
                self.assertEqual(str(request.url).split("?")[0], "https://api.minimax.cn/anthropic/v1/messages")
                self.assertEqual(request.headers["x-api-key"], "fixture-only-key")
                self.assertIn("anthropic-version", request.headers)
            else:
                self.assertEqual(str(request.url), "https://api.stepfun.com/v1/chat/completions")
                self.assertEqual(request.headers["authorization"], "Bearer fixture-only-key")
                self.assertNotIn("strict", body["tools"][0]["function"])
        for _, body in calls[1:]:
            assistant = next(message for message in body["messages"] if message["role"] == "assistant")
            if code == "minimax":
                self.assertEqual(assistant["content"][0], {
                    "type": "thinking", "thinking": "Add the two values.", "signature": "fixture-signature",
                })
                self.assertEqual(assistant["content"][1]["id"], "call-add")
                tool_results = [block for message in body["messages"] if message["role"] == "user"
                                and isinstance(message["content"], list) for block in message["content"]
                                if block["type"] == "tool_result"]
                self.assertEqual(len(tool_results), 1)
                self.assertEqual(tool_results[0]["tool_use_id"], "call-add")
                self.assertEqual(tool_results[0]["content"], [{"type": "text", "text": "260"}])
            else:
                self.assertEqual(assistant["reasoning_content"], "Add the two values.")
                self.assertEqual(assistant["tool_calls"][0]["id"], "call-add")
                returns = [message for message in body["messages"] if message["role"] == "tool"]
                self.assertEqual(returns, [{"role": "tool", "tool_call_id": "call-add", "content": "260"}])
        return calls

    async def test_minimax_nonstream_tool_result_and_reopened_thinking_signature(self):
        await self._tool_round_trip("minimax", False)

    async def test_minimax_stream_tool_result_and_reopened_thinking_signature(self):
        await self._tool_round_trip("minimax", True)

    async def test_stepfun_nonstream_token_limit_and_reasoning_round_trip(self):
        await self._tool_round_trip("stepfun", False)

    async def test_stepfun_stream_token_limit_and_reasoning_round_trip(self):
        await self._tool_round_trip("stepfun", True)

    async def test_generic_anthropic_respects_customer_path_and_foreign_model_id(self):
        calls = []

        def respond(request):
            calls.append(request)
            return _anthropic_reply(tools=False, stream=False)

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
            with patch("deep_agent.models.provider_registry.AnthropicProvider",
                       side_effect=lambda **kw: AnthropicProvider(http_client=client, **kw)):
                model = resolve_provider_model(model_name="customer-deepseek", provider=_config(
                    "anthropic_compatible", custom="https://customer.example/proxy/anthropic",
                ))
            result = await Agent(model).run("Reply")
        self.assertEqual(result.output, "260")
        self.assertEqual(str(calls[0].url).split("?")[0], "https://customer.example/proxy/anthropic/v1/messages")
        self.assertEqual(json.loads(calls[0].content)["model"], "customer-deepseek")
        self.assertEqual(calls[0].headers["x-api-key"], "fixture-only-key")

    async def test_authentication_errors_keep_status_without_endpoint_fallback(self):
        for code, provider_class, provider_symbol in (
            ("minimax", MiniMaxProvider, "MiniMaxProvider"),
            ("stepfun", StepFunProvider, "StepFunProvider"),
            ("anthropic_compatible", AnthropicProvider, "AnthropicProvider"),
        ):
            with self.subTest(provider_type=code):
                calls = []

                def respond(request):
                    calls.append(request)
                    return httpx2.Response(401, json={"type": "error", "error": {
                        "type": "authentication_error", "message": "Fixture credential invalid",
                    }})

                async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
                    config = _config(code, custom="https://customer.example/api" if code == "anthropic_compatible" else None)
                    with patch(f"deep_agent.models.provider_registry.{provider_symbol}",
                               side_effect=lambda **kw: provider_class(http_client=client, **kw)):
                        model = resolve_provider_model(model_name="fixture-model", provider=config)
                    with self.assertRaises(ModelHTTPError) as raised:
                        await Agent(model).run("Reply")
                self.assertEqual(raised.exception.status_code, 401)
                self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
