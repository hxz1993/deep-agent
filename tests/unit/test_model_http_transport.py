"""Exercise concurrent SDK reads through real HTTPX content decoders."""
import asyncio
import json
import unittest
from unittest.mock import patch

import httpx2
from pydantic_ai import Agent
from pydantic_ai.models import ModelRequestParameters

try:
    import zstandard
except ImportError:
    zstandard = None

from deep_agent.agent.title_generator import generate_title
from deep_agent.models.http_transport import model_http_settings
from deep_agent.models.provider_registry import PROVIDER_TYPES, ProviderRuntimeConfig, resolve_provider_model


class ModelHttpTransportTests(unittest.IsolatedAsyncioTestCase):
    def test_preserves_settings_and_headers_without_mutating_input(self):
        original = {"max_tokens": 64, "extra_headers": {"X-Route": "fixture", "accept-ENCODING": "zstd"}}
        result = model_http_settings(original)
        self.assertEqual(result, {"max_tokens": 64, "extra_headers": {
            "X-Route": "fixture", "Accept-Encoding": "identity"}})
        self.assertEqual(original["extra_headers"]["accept-ENCODING"], "zstd")

    async def test_model_defaults_cover_registered_http_protocols(self):
        for definition in PROVIDER_TYPES:
            if definition.model_protocol == "openai_codex":
                continue  # Its credential-backed cache is covered by Codex tests.
            with self.subTest(provider=definition.code):
                name = "anthropic/claude-sonnet-4" if definition.code == "openrouter" else "fixture"
                model = resolve_provider_model(model_name=name, provider=ProviderRuntimeConfig(
                    id="fixture", name="fixture", provider_type=definition.code, api_key="fixture",
                    use_default_base_url=definition.supports_default_base_url,
                    base_url="https://example.invalid/v1"))
                try:
                    settings, _ = model.prepare_request({"max_tokens": 96}, ModelRequestParameters())
                    self.assertEqual(settings["extra_headers"], {"Accept-Encoding": "identity"})
                    self.assertEqual(settings["max_tokens"], 96)
                finally:
                    async with model:
                        pass

    async def test_google_and_anthropic_forward_transport_header(self):
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.anthropic import AnthropicProvider
        from pydantic_ai.providers.google import GoogleProvider

        for family in ("google", "anthropic"):
            with self.subTest(family=family):
                requests = []
                def handle(request):
                    requests.append(request)
                    body = ({"candidates": [{"content": {"parts": [{"text": "OK"}], "role": "model"},
                              "finishReason": "STOP"}]} if family == "google" else
                            {"id": "message-fixture", "type": "message", "role": "assistant", "model": "fixture",
                             "content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn",
                             "usage": {"input_tokens": 1, "output_tokens": 1}})
                    return httpx2.Response(200, json=body)

                async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
                    provider_class, model_class = ((GoogleProvider, GoogleModel) if family == "google"
                                                    else (AnthropicProvider, AnthropicModel))
                    provider = provider_class(api_key="fixture", http_client=client)
                    model = model_class("fixture", provider=provider, settings=model_http_settings())
                    try:
                        result = await Agent(model).run("测试")
                        self.assertEqual(result.output, "OK")
                        self.assertEqual(requests[0].headers["accept-encoding"], "identity")
                    finally:
                        if family == "google":
                            await provider.client.aio.aclose()

    @unittest.skipIf(zstandard is None, "zstd backend is optional and absent")
    async def test_concurrent_main_stream_and_title_avoid_shared_zstd_decoder(self):
        barrier = asyncio.Barrier(2)
        requests, streams = [], []

        class Body(httpx2.AsyncByteStream):
            closed = False

            def __init__(self, data):
                self.data = data

            async def __aiter__(self):
                # Both response decoders see a partial frame before either
                # receives the rest. Serial/fully buffered tests miss the bug.
                yield self.data[:1]
                await barrier.wait()
                yield self.data[1:]

            async def aclose(self):
                self.closed = True

        def handle(request):
            requests.append(request)
            streaming = json.loads(request.content).get("stream", False)
            if streaming:
                chunk = {"id": "chat-stream", "object": "chat.completion.chunk", "created": 0,
                         "model": "fixture", "choices": [{"index": 0,
                         "delta": {"role": "assistant", "content": "回复成功"}, "finish_reason": None}]}
                end = {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                data = ("data: " + json.dumps(chunk) + "\n\ndata: " + json.dumps(end)
                        + "\n\ndata: [DONE]\n\n").encode()
                content_type = "text/event-stream"
            else:
                data = json.dumps({"id": "chat-title", "object": "chat.completion", "created": 0,
                    "model": "fixture", "choices": [{"index": 0, "message": {
                        "role": "assistant", "content": "并发请求测试"}, "finish_reason": "stop"}]}).encode()
                content_type = "application/json"
            headers = {"content-type": content_type}
            if "zstd" in request.headers.get("accept-encoding", ""):
                data = zstandard.ZstdCompressor().compress(data)
                headers["content-encoding"] = "zstd"
            stream = Body(data)
            streams.append(stream)
            return httpx2.Response(200, headers=headers, stream=stream)

        client = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
        try:
            with patch("pydantic_ai.providers._openai_compatible.create_async_httpx2_client", return_value=client):
                model = resolve_provider_model(model_name="fixture", provider=ProviderRuntimeConfig(
                    id="fixture", name="fixture", provider_type="openai_compatible", api_key="fixture",
                    use_default_base_url=False, base_url="https://example.invalid/v1"))
                model.client.max_retries = 0

            async def main():
                async with Agent(model).run_stream("测试") as result:
                    return await result.get_output()

            try:
                output, title = await asyncio.wait_for(asyncio.gather(
                    main(), generate_title(model=model, user_text="测试")), timeout=5)
            except TimeoutError:
                self.fail(f"request count={len(requests)}, streams={len(streams)}, barrier={barrier.n_waiting}")
            self.assertEqual(output, "回复成功")
            self.assertIsNotNone(title)
            self.assertEqual(title.title, "并发请求测试")
            self.assertEqual(len(requests), 2)
            self.assertTrue(all(r.headers["accept-encoding"] == "identity" for r in requests))
            self.assertTrue(all(s.closed for s in streams))
        finally:
            await client.aclose()
        self.assertTrue(client.is_closed)
