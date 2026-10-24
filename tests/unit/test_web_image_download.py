from __future__ import annotations

import asyncio
import base64
import io
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx2
from PIL import Image
from pydantic_ai import Agent, ToolFailed
from pydantic_ai.messages import BinaryContent, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from deep_agent.web.proxy_compat import install_proxy_compatibility
from deep_agent.web.runtime import _web_image_view_tool


URL = "https://93.184.216.34/poster.jpg"


def image_bytes(fmt="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (4, 3), "red").save(stream, format=fmt)
    return stream.getvalue()


class WebImageDownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_proxy_compatibility()

    async def test_downloads_inside_tool_for_both_legacy_flag_values(self):
        data = image_bytes()
        for flag in (False, True):
            async def send(_client, request):
                self.assertNotIn("authorization", request.headers)
                self.assertNotIn("cookie", request.headers)
                return httpx2.Response(200, content=data, headers={"content-type": "image/jpeg"}, request=request)

            with self.subTest(flag=flag), patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)) as transport:
                result = await _web_image_view_tool().function(URL, force_download=flag)
            transport.assert_awaited_once()
            self.assertIsInstance(result.return_value, BinaryContent)
            self.assertEqual(result.return_value.data, data)
            self.assertEqual(result.return_value.media_type, "image/png")
            self.assertIsNone(result.content)
            self.assertEqual(result.metadata["sources"][0]["url"], URL)

    async def test_404_is_returned_as_tool_failure_before_any_model_mapping(self):
        async def send(_client, request):
            return httpx2.Response(404, content=b"missing", request=request)

        for flag in (False, True):
            with self.subTest(flag=flag), patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)) as transport:
                with self.assertRaisesRegex(ToolFailed, "404"):
                    await _web_image_view_tool().function(URL, force_download=flag)
            transport.assert_awaited_once()

    async def test_html_and_corrupt_images_are_failures_even_with_jpg_extension(self):
        for data in (b"<html>Not an image</html>", image_bytes()[:40], b""):
            async def send(_client, request):
                return httpx2.Response(200, content=data, headers={"content-type": "image/jpeg"}, request=request)

            with self.subTest(data=data[:20]), patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)):
                with self.assertRaises(ToolFailed):
                    await _web_image_view_tool().function(URL)

    async def test_jpeg_with_valid_header_but_truncated_pixels_is_rejected(self):
        data = image_bytes("JPEG")[:-2]
        # Pillow verify() alone accepts this truncated JPEG.
        with Image.open(io.BytesIO(data)) as image:
            image.verify()

        async def send(_client, request):
            return httpx2.Response(200, content=data, request=request)

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)):
            with self.assertRaises(ToolFailed):
                await _web_image_view_tool().function(URL)

    async def test_extensionless_image_and_redirect_keep_verified_public_source(self):
        data = image_bytes("JPEG")
        async def send(_client, request):
            if request.url.path == "/render":
                return httpx2.Response(302, headers={"location": "/actual"}, request=request)
            return httpx2.Response(200, content=data, headers={"content-type": "application/octet-stream"}, request=request)

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)) as transport:
            result = await _web_image_view_tool().function("https://93.184.216.34/render")
        self.assertEqual(transport.await_count, 2)
        self.assertEqual(result.return_value.media_type, "image/jpeg")
        self.assertEqual(result.metadata["sources"][0]["url"], "https://93.184.216.34/actual")

    async def test_private_redirect_never_reaches_the_private_host(self):
        async def send(_client, request):
            return httpx2.Response(302, headers={"location": "http://127.0.0.1/private.png"}, request=request)

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)) as transport:
            with self.assertRaises(ToolFailed):
                await _web_image_view_tool().function(URL)
        transport.assert_awaited_once()

    async def test_cancel_is_not_converted_to_a_tool_error(self):
        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=asyncio.CancelledError)) as transport:
            with self.assertRaises(asyncio.CancelledError):
                await _web_image_view_tool().function(URL)
        transport.assert_awaited_once()

    async def test_oversized_response_is_rejected_before_decoding(self):
        async def send(_client, request):
            return httpx2.Response(200, content=b"x" * (20 * 1024 * 1024 + 1), request=request)

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)), \
                patch("deep_agent.web.runtime._verified_image_media_type") as verify:
            with self.assertRaisesRegex(ToolFailed, "maximum size"):
                await _web_image_view_tool().function(URL)
        verify.assert_not_called()

    async def test_timeout_has_bounded_retries_then_returns_failure(self):
        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=httpx2.ReadTimeout("timed out"))) as transport, \
                patch("deep_agent.files.public_download.asyncio.sleep", AsyncMock()):
            with self.assertRaisesRegex(ToolFailed, "timed out"):
                await _web_image_view_tool().function(URL)
        self.assertEqual(transport.await_count, 2)

    async def test_image_pixel_bomb_is_rejected(self):
        async def send(_client, request):
            return httpx2.Response(200, content=image_bytes(), request=request)

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)), \
                patch.object(Image, "MAX_IMAGE_PIXELS", 4):
            with self.assertRaises(ToolFailed):
                await _web_image_view_tool().function(URL)

    async def test_actual_provider_wire_contains_validated_bytes_without_later_url_download(self):
        data = image_bytes()
        wire = []

        async def image_transport(_client, request):
            return httpx2.Response(200, content=data, request=request)

        def model_transport(request):
            wire.append(json.loads(request.content))
            message = ({"role": "assistant", "tool_calls": [{"id": "image-call", "type": "function",
                        "function": {"name": "web_image_view", "arguments": json.dumps({"url": URL})}}]}
                       if len(wire) == 1 else {"role": "assistant", "content": "verified image"})
            return httpx2.Response(200, json={"id": "fixture", "object": "chat.completion", "created": 1,
                "model": "gpt-4.1", "choices": [{"index": 0, "message": message,
                "finish_reason": "tool_calls" if len(wire) == 1 else "stop"}]})

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=image_transport)) as download:
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(model_transport)) as client:
                model = OpenAIChatModel("gpt-4.1", provider=OpenAIProvider(api_key="fixture-only", http_client=client))
                result = await Agent(model, tools=[_web_image_view_tool()]).run("inspect")
        self.assertEqual(result.output, "verified image")
        self.assertEqual(len(wire), 2)
        urls = [part["image_url"]["url"] for message in wire[1]["messages"]
                if isinstance(message.get("content"), list) for part in message["content"]
                if part.get("type") == "image_url"]
        self.assertEqual(urls, ["data:image/png;base64," + base64.b64encode(data).decode()])
        download.assert_awaited_once()

    async def test_model_can_choose_a_different_image_after_404(self):
        seen = []
        data = image_bytes()
        async def send(_client, request):
            seen.append(request.url.path)
            status = 404 if request.url.path == "/poster.jpg" else 200
            return httpx2.Response(status, content=(b"missing" if status == 404 else data), request=request)

        def model(messages, info):
            returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
            if not returns:
                return ModelResponse(parts=[ToolCallPart("web_image_view", {"url": URL}, "bad")])
            if len(returns) == 1:
                self.assertEqual(returns[0].outcome, "failed")
                self.assertIn("404", str(returns[0].content))
                return ModelResponse(parts=[ToolCallPart("web_image_view", {"url": URL.replace("poster", "valid")}, "good")])
            self.assertIsInstance(returns[-1].content, BinaryContent)
            self.assertEqual(returns[-1].content.data, data)
            return ModelResponse(parts=[TextPart("看图成功")])

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)):
            result = await Agent(FunctionModel(model), tools=[_web_image_view_tool()]).run("找一张图")
        self.assertEqual(result.output, "看图成功")
        self.assertEqual(seen, ["/poster.jpg", "/valid.jpg"])
