"""URL media keeps existing channel authorization, limits and preflight atomicity."""
from __future__ import annotations

import asyncio
import io
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from pydantic import ValidationError
from pydantic_ai import ModelRetry

from deep_agent.channels.message_tool import MessageComponent, build_channel_message_toolset, prepare_components
from deep_agent.channels.outbound import DeliveryError
from tests.unit.test_channel_message_tool import caps, context, source


def png():
    output = io.BytesIO()
    Image.new("RGB", (2, 2)).save(output, "PNG")
    return output.getvalue()


class ChannelURLTests(unittest.IsolatedAsyncioTestCase):
    def test_media_source_union_preserves_cards_and_file_ids(self):
        self.assertEqual(MessageComponent(type="image", file_id="known").file_id, "known")
        self.assertEqual(MessageComponent(type="image", url="https://example.test/a.png").type, "image")
        for args in (
            {"type": "image"}, {"type": "image", "url": " "},
            {"type": "image", "file_id": "known", "url": "https://example.test/a.png"},
            {"type": "image", "file_id": "", "url": "https://example.test/a.png"},
            {"type": "text", "text": "hello", "url": "https://example.test/a.png"},
            {"type": "image", "url": "https://example.test/a.png", "text": "hello"},
            {"type": "textcard", "url": "https://example.test", "payload": {
                "title": "title", "description": "body", "url": "https://example.test"}},
        ):
            with self.subTest(args=args), self.assertRaises(ValidationError):
                MessageComponent(**args)
        MessageComponent(type="textcard", payload={
            "title": "title", "description": "body", "url": "https://example.test"})

    async def test_url_media_is_call_local_without_file_service_or_file_id_grants(self):
        deps = context().deps
        deps.file_store = SimpleNamespace(upload=AsyncMock(), download_bounded=AsyncMock())
        data = png()
        with patch("deep_agent.channels.message_tool.load_public_file", AsyncMock(
            return_value=(data, "remote.png", "image/png", "https://example.test/a.png"),
        )) as load:
            result = await prepare_components(deps, [MessageComponent(type="image", url="https://example.test/a.png")], caps())
        self.assertEqual(result[0].data, data)
        self.assertEqual(result[0].kind, "image")
        self.assertEqual(load.await_args.kwargs["max_bytes"], 2000)
        self.assertEqual(deps.session_file_artifacts, {})
        deps.file_store.upload.assert_not_awaited()
        deps.file_store.download_bounded.assert_not_awaited()

    async def test_url_download_obeys_remaining_batch_capacity_and_sanitizes_name(self):
        async def download(url, *, max_bytes):
            return b"x" * 60, "../remote.txt", "text/plain", url
        with patch("deep_agent.channels.message_tool.max_upload_bytes", return_value=100), \
                patch("deep_agent.channels.message_tool.load_public_file", AsyncMock(side_effect=download)) as load:
            # A compliant downloader rejects the second response at its 40-byte
            # bound. Deliberately exercise the preflight's independent guard too.
            with self.assertRaises(DeliveryError):
                await prepare_components(context().deps, [MessageComponent(type="file", url="https://example.test/a")]*2, caps())
        self.assertEqual([x.kwargs["max_bytes"] for x in load.await_args_list], [100, 40])

    async def test_unsupported_kind_is_rejected_before_public_download(self):
        with patch("deep_agent.channels.message_tool.load_public_file", AsyncMock()) as load:
            with self.assertRaises(DeliveryError):
                await prepare_components(context().deps, [MessageComponent(type="image", url="https://example.test/a")],
                                         replace(caps(), kinds=("text",), file_limit=0))
        load.assert_not_awaited()

    async def test_late_url_preflight_failure_prevents_entire_send(self):
        gateway = SimpleNamespace(send_message=AsyncMock())
        function = build_channel_message_toolset(None, gateway, source(), caps()).tools["channel_message"].function
        with patch("deep_agent.channels.message_tool.resolve_run_context", AsyncMock(return_value=source())), \
                patch("deep_agent.channels.message_tool.load_public_file", AsyncMock(side_effect=ModelRetry("public file unavailable"))):
            with self.assertRaises(ModelRetry):
                await function(context(), [MessageComponent(type="text", text="first"),
                                           MessageComponent(type="image", url="https://example.test/a")])
        gateway.send_message.assert_not_awaited()

    async def test_malformed_url_is_rejected_before_dispatch_not_unknown_delivery(self):
        gateway = SimpleNamespace(send_message=AsyncMock())
        function = build_channel_message_toolset(None, gateway, source(), caps()).tools["channel_message"].function
        for url in ("https://[invalid]/a.png", "https://[::1/a.png"):
            with self.subTest(url=url), patch(
                "deep_agent.channels.message_tool.resolve_run_context", AsyncMock(return_value=source()),
            ), patch("deep_agent.files.public_download.safe_download", AsyncMock()) as download:
                with self.assertRaisesRegex(ModelRetry, "有效的绝对 HTTP"):
                    await function(context(), [MessageComponent(type="image", url=url)])
                download.assert_not_awaited()
        gateway.send_message.assert_not_awaited()

    async def test_cancellation_token_set_during_download_stops_dispatch(self):
        ctx = context()
        ctx.deps.cancellation_token = SimpleNamespace(cancelled=False)
        gateway = SimpleNamespace(send_message=AsyncMock())
        async def download(url, *, max_bytes):
            ctx.deps.cancellation_token.cancelled = True
            return png(), "a.png", "image/png", url
        function = build_channel_message_toolset(None, gateway, source(), caps()).tools["channel_message"].function
        with patch("deep_agent.channels.message_tool.resolve_run_context", AsyncMock(return_value=source())), \
                patch("deep_agent.channels.message_tool.load_public_file", side_effect=download):
            with self.assertRaises(asyncio.CancelledError):
                await function(ctx, [MessageComponent(type="image", url="https://example.test/a")])
        gateway.send_message.assert_not_awaited()
