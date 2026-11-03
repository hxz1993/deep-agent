"""Public URL security and real SDK coverage through the unified viewer."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import struct
import unittest
import zlib
from unittest.mock import AsyncMock, patch

import httpx2
from PIL import Image
from pydantic_ai import Agent, ModelRetry, RunContext, ToolFailed
from pydantic_ai.messages import BinaryContent, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.files.image_view.runtime import build_view_image_runtime
from deep_agent.web.proxy_compat import install_proxy_compatibility


URL = "https://93.184.216.34/poster.jpg"


def image_bytes(fmt="PNG"):
    stream = io.BytesIO()
    with Image.new("RGB", (4, 3), "red") as image:
        image.save(stream, format=fmt)
    return stream.getvalue()


def url_args(url=URL, *, pages=None):
    item = {"resource": {"type": "url", "url": url}}
    if pages is not None:
        item["pages"] = pages
    return {"items": [item]}


def pixels(result):
    return [item for item in result.return_value if isinstance(item, BinaryContent)]


class WebImageDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        install_proxy_compatibility()
        self.deps = AgentDeps(session_id="public-viewer", request_headers={"Authorization": "fixture-user", "Cookie": "fixture-cookie"})
        self.ctx = RunContext(deps=self.deps, model=TestModel(), usage=RunUsage())
        self.runtime = build_view_image_runtime({}, image_capable=True, web_authorized=True, python_available=False)
        self.viewer = (await self.runtime.toolset.get_tools(self.ctx))["view_image"]

    async def asyncTearDown(self):
        if self.deps.image_view_state is not None:
            await self.deps.image_view_state.aclose()
            self.deps.image_view_state = None

    async def view(self, url=URL, *, pages=None):
        return await self.viewer.call_func(url_args(url, pages=pages), self.ctx)

    async def test_downloads_without_credentials_and_returns_verified_native_image(self):
        async def send(_client, request):
            self.assertNotIn("authorization", request.headers)
            self.assertNotIn("cookie", request.headers)
            return httpx2.Response(200, content=image_bytes(), headers={"content-type": "image/jpeg"}, request=request)

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)) as transport:
            result = await self.view()
        transport.assert_awaited_once()
        self.assertEqual(len(pixels(result)), 1)
        self.assertEqual(pixels(result)[0].media_type, "image/png")
        self.assertEqual(result.metadata["view_image"]["items"][0]["resource"], {"type": "url", "url": URL})
        self.assertTrue(result.metadata["web_content_untrusted"])

    async def test_new_schema_omits_legacy_force_download_flag(self):
        self.assertNotIn("force_download", json.dumps(self.viewer.tool_def.parameters_json_schema))
        self.assertEqual(self.viewer.tool_def.name, "view_image")

    async def test_404_is_a_tool_failure_before_any_model_mapping(self):
        async def send(_client, request):
            return httpx2.Response(404, content=b"missing", request=request)

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)) as transport:
            with self.assertRaisesRegex(ToolFailed, "404"):
                await self.view()
        transport.assert_awaited_once()

    async def test_html_and_corrupt_images_fail_even_with_jpg_extension(self):
        for index, data in enumerate((b"<html>Not an image</html>", image_bytes()[:40], b"")):
            async def send(_client, request):
                return httpx2.Response(200, content=data, headers={"content-type": "image/jpeg"}, request=request)

            with self.subTest(data=data[:20]), patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)):
                with self.assertRaises(ToolFailed):
                    await self.view(f"{URL}?invalid={index}")

    async def test_webp_and_gif_sources_return_normalized_native_pixels(self):
        for fmt in ("WEBP", "GIF"):
            async def send(_client, request):
                return httpx2.Response(200, content=image_bytes(fmt), headers={"content-type": "application/octet-stream"}, request=request)

            with self.subTest(fmt=fmt), patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)):
                result = await self.view(f"{URL}?format={fmt}")
            item = result.metadata["view_image"]["items"][0]
            self.assertEqual(item["format"], fmt.lower())
            self.assertEqual(item["gif_first_frame"], fmt == "GIF")
            self.assertEqual(pixels(result)[0].media_type, "image/png")

    async def test_jpeg_with_valid_header_but_truncated_pixels_is_rejected(self):
        data = image_bytes("JPEG")[:-2]
        with Image.open(io.BytesIO(data)) as image:
            image.verify()  # JPEG verify() alone accepts this truncated body.

        async def send(_client, request):
            return httpx2.Response(200, content=data, request=request)

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)):
            with self.assertRaises(ToolFailed):
                await self.view()

    async def test_extensionless_image_and_redirect_keep_verified_public_source(self):
        async def send(_client, request):
            if request.url.path == "/render":
                return httpx2.Response(302, headers={"location": "/actual"}, request=request)
            return httpx2.Response(200, content=image_bytes("JPEG"), headers={"content-type": "application/octet-stream"}, request=request)

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)) as transport:
            result = await self.view("https://93.184.216.34/render")
        self.assertEqual(transport.await_count, 2)
        item = result.metadata["view_image"]["items"][0]
        self.assertEqual(item["format"], "jpeg")
        self.assertEqual(item["resource"], {"type": "url", "url": "https://93.184.216.34/actual"})
        self.assertEqual(pixels(result)[0].media_type, "image/png")

    async def test_private_redirect_never_reaches_the_private_host(self):
        async def send(_client, request):
            return httpx2.Response(302, headers={"location": "http://127.0.0.1/private.png"}, request=request)

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)) as transport:
            with self.assertRaises(ToolFailed):
                await self.view()
        transport.assert_awaited_once()

    async def test_private_metadata_credentialed_and_malformed_urls_do_not_download(self):
        for url in ("http://127.0.0.1/chart.png", "http://169.254.169.254/latest/meta-data/",
                    "https://user:secret@example.com/chart.png", "https://[broken/", "file:///tmp/chart.png"):
            with self.subTest(url=url), patch("pydantic_ai._ssrf._send_request", AsyncMock()) as transport:
                with self.assertRaises((ModelRetry, ToolFailed)):
                    await self.view(url)
            transport.assert_not_awaited()

    async def test_cancel_is_not_converted_to_a_tool_error(self):
        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=asyncio.CancelledError)) as transport:
            with self.assertRaises(asyncio.CancelledError):
                await self.view()
        transport.assert_awaited_once()

    async def test_oversized_response_is_rejected_before_rendering(self):
        async def send(_client, request):
            return httpx2.Response(200, content=b"x" * (20 * 1024 * 1024 + 1), request=request)

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)), patch(
            "deep_agent.files.image_view.toolset.render_source", new_callable=AsyncMock,
        ) as renderer:
            with self.assertRaisesRegex(ToolFailed, "maximum size"):
                await self.view()
        renderer.assert_not_awaited()

    async def test_timeout_has_bounded_retries_then_returns_failure(self):
        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=httpx2.ReadTimeout("timed out"))) as transport, patch(
            "deep_agent.files.public_download.asyncio.sleep", AsyncMock(),
        ):
            with self.assertRaisesRegex(ToolFailed, "timed out"):
                await self.view()
        self.assertEqual(transport.await_count, 2)

    async def test_pixel_bomb_is_rejected_in_real_worker(self):
        original = image_bytes()
        header = struct.pack(">IIBBBBB", 8001, 8000, 8, 2, 0, 0, 0)
        # A valid PNG header whose claimed dimensions exceed the pixel budget:
        # reject before trying to decompress any deliberately tiny image body.
        data = original[:16] + header + struct.pack(">I", zlib.crc32(b"IHDR" + header)) + original[33:]

        async def send(_client, request):
            return httpx2.Response(200, content=data, request=request)

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)):
            with self.assertRaisesRegex(ToolFailed, "6400"):
                await self.view()

    async def test_public_pdf_selected_pages_use_same_actual_viewer(self):
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=160)
        writer.add_blank_page(width=120, height=180)
        stream = io.BytesIO()
        writer.write(stream)

        async def send(_client, request):
            return httpx2.Response(200, content=stream.getvalue(), headers={"content-type": "image/jpeg"}, request=request)

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)):
            result = await self.view(pages=[2, 1])
        items = result.metadata["view_image"]["items"]
        self.assertEqual([item["page"] for item in items], [2, 1])
        self.assertTrue(all(item["format"] == "pdf" and item["page_count"] == 2 for item in items))
        self.assertEqual(len(pixels(result)), 2)

    async def test_actual_provider_wire_contains_pixels_without_later_url_download(self):
        wire = []

        async def image_transport(_client, request):
            return httpx2.Response(200, content=image_bytes(), request=request)

        def model_transport(request):
            wire.append(json.loads(request.content))
            message = ({"role": "assistant", "tool_calls": [{"id": "image-call", "type": "function",
                        "function": {"name": "view_image", "arguments": json.dumps(url_args())}}]}
                       if len(wire) == 1 else {"role": "assistant", "content": "verified image"})
            return httpx2.Response(200, json={"id": "fixture", "object": "chat.completion", "created": 1,
                "model": "gpt-4.1", "choices": [{"index": 0, "message": message,
                "finish_reason": "tool_calls" if len(wire) == 1 else "stop"}]})

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=image_transport)) as download:
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(model_transport)) as client:
                model = OpenAIChatModel("gpt-4.1", provider=OpenAIProvider(api_key="fixture-only", http_client=client))
                result = await Agent(model, deps_type=AgentDeps, toolsets=[self.runtime.toolset],
                                     capabilities=[self.runtime.lifecycle]).run("inspect", deps=self.deps)
        self.assertEqual(result.output, "verified image")
        self.assertEqual(len(wire), 2)
        urls = [part["image_url"]["url"] for message in wire[1]["messages"]
                if isinstance(message.get("content"), list) for part in message["content"]
                if part.get("type") == "image_url"]
        self.assertEqual(len(urls), 1)
        self.assertTrue(urls[0].startswith("data:image/png;base64,"))
        with Image.open(io.BytesIO(base64.b64decode(urls[0].split(",", 1)[1]))) as image:
            self.assertEqual(image.size, (4, 3))
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
        download.assert_awaited_once()

    async def test_model_can_choose_a_different_image_after_404(self):
        seen = []

        async def send(_client, request):
            seen.append(request.url.path)
            status = 404 if request.url.path == "/poster.jpg" else 200
            return httpx2.Response(status, content=(b"missing" if status == 404 else image_bytes()), request=request)

        def model(messages, info):
            returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
            if not returns:
                return ModelResponse(parts=[ToolCallPart("view_image", url_args(), "bad")])
            if len(returns) == 1:
                self.assertEqual(returns[0].outcome, "failed")
                self.assertIn("404", str(returns[0].content))
                return ModelResponse(parts=[ToolCallPart("view_image", url_args(URL.replace("poster", "valid")), "good")])
            actual = [item for item in returns[-1].content if isinstance(item, BinaryContent)]
            self.assertEqual(len(actual), 1)
            self.assertEqual(actual[0].media_type, "image/png")
            return ModelResponse(parts=[TextPart("看图成功")])

        with patch("pydantic_ai._ssrf._send_request", AsyncMock(side_effect=send)):
            result = await Agent(FunctionModel(model), deps_type=AgentDeps, toolsets=[self.runtime.toolset],
                                 capabilities=[self.runtime.lifecycle]).run("找一张图", deps=self.deps)
        self.assertEqual(result.output, "看图成功")
        self.assertEqual(seen, ["/poster.jpg", "/valid.jpg"])
