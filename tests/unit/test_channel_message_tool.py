"""Current-chat tool contract and media preflight, without channel side effects."""
import io
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from pydantic import ValidationError
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.channels.message_tool import MessageComponent, build_channel_message_toolset, normalize_media, prepare_components
from deep_agent.channels.outbound import ChannelRunContext, DeliveryError, MessageCapabilities, MessageTarget, PreparedComponent
from deep_agent.schemas.attachment import AttachmentRef


def box(kind, payload, *, extended=False, to_end=False):
    if to_end:
        return b"\0" * 4 + kind + payload
    if extended:
        return b"\0\0\0\1" + kind + (len(payload) + 16).to_bytes(8, "big") + payload
    return (len(payload) + 8).to_bytes(4, "big") + kind + payload


def mp4_container(*, brand=b"isom", handler=b"vide", extended=False, to_end=False):
    ftyp = box(b"ftyp", brand + b"\0" * 4 + b"isom", extended=extended)
    track = box(b"trak", box(b"mdia", box(b"hdlr", b"\0" * 8 + handler + b"\0" * 12)))
    return ftyp + box(b"moov", track, extended=extended, to_end=to_end)


def caps(reply=True):
    return MessageCapabilities(("text", "image", "file", "audio", "video"), reply, 100, 2000, 500, 1000, 1000, audio_formats=("opus",))


def source():
    return ChannelRunContext("account", "channel-run", "session", "agent", "user", MessageTarget("account", "single", "peer", "msg"))


def context():
    return RunContext(deps=AgentDeps(session_id="session", agent_id="agent", user="user", run_id="run"),
                      model=TestModel(), usage=RunUsage(), tool_call_id="call")


class MessageToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_strict_channel_requires_explicit_file_for_invalid_native_media(self):
        strict = replace(caps(), media_fallback_to_file=False)
        for item in (PreparedComponent("audio", data=b"RIFFnot-opus", filename="audio.wav"),
                     PreparedComponent("audio", data=b"OggSOpusHead" + b"x" * 1100),
                     PreparedComponent("video", data=b"invalid", filename="video.mp4")):
            with self.assertRaises(DeliveryError):
                normalize_media(item, strict)
            self.assertEqual(normalize_media(replace(item, kind="file"), strict).kind, "file")

    async def test_schema_has_no_routing_fields_and_quote_depends_on_capability(self):
        for supports in (True, False):
            tools = await build_channel_message_toolset(None, None, source(), caps(supports)).get_tools(context())
            self.assertEqual(set(tools), {"channel_message"})
            schema = tools["channel_message"].tool_def.parameters_json_schema
            self.assertEqual(set(schema["properties"]), {"components", "action", *(["reply_to"] if supports else [])})
            self.assertNotIn("account_id", json.dumps(schema))
            self.assertFalse(schema["additionalProperties"])
        with self.assertRaises(ValidationError):
            MessageComponent(type="file", file_id="x", target="other")
        for kwargs in ({"type": "text", "text": " "}, {"type": "text", "text": "x", "file_id": "f"}, {"type": "file", "text": "x", "file_id": "f"}):
            with self.assertRaises(ValidationError):
                MessageComponent(**kwargs)

    async def test_unauthorized_file_is_never_downloaded(self):
        deps = context().deps; deps.file_store = SimpleNamespace(download_bounded=AsyncMock())
        with self.assertRaises(DeliveryError) as caught:
            await prepare_components(deps, [MessageComponent(type="file", file_id="foreign")], caps())
        self.assertEqual(caught.exception.code, "file_not_authorized")
        deps.file_store.download_bounded.assert_not_awaited()

    async def test_uploaded_and_generated_files_are_authorized_and_headers_preserved(self):
        deps = context().deps
        deps.attachment_by_id = {"upload": AttachmentRef(id="upload", name="original.txt", mime="text/plain")}
        deps.session_file_artifacts = {"output": {"file_name": "artifact.csv", "mime": "text/csv"}}
        deps.request_headers = {"User": "owner"}
        deps.file_store = SimpleNamespace(download_bounded=AsyncMock(return_value=b"bytes"))
        prepared = await prepare_components(deps, [MessageComponent(type="file", file_id="upload", display_name="../renamed.txt"), MessageComponent(type="file", file_id="output")], caps())
        self.assertEqual([p.filename for p in prepared], ["renamed.txt", "artifact.csv"])
        self.assertEqual([p.data for p in prepared], [b"bytes", b"bytes"])
        self.assertEqual(deps.file_store.download_bounded.call_args.args, ("output", {"User": "owner"}))

    async def test_media_is_sniffed_not_trusted_from_extension(self):
        item = normalize_media(PreparedComponent("image", data=b"not a png", filename="fake.png", requested_kind="image"), caps())
        self.assertEqual((item.kind, item.requested_kind), ("file", "image"))
        image = io.BytesIO(); Image.new("RGB", (2, 2)).save(image, "PNG")
        self.assertEqual(normalize_media(PreparedComponent("image", data=image.getvalue()), caps()).kind, "image")
        for data, kind, expected in ((b"OggS"+b"OpusHead", "audio", "audio"), (b"#!AMR\nrest", "audio", "file"), (mp4_container(), "video", "video")):
            self.assertEqual(normalize_media(PreparedComponent(kind, data=data), caps()).kind, expected)

    async def test_mp4_requires_supported_brand_and_video_handler(self):
        for data in (mp4_container(), mp4_container(extended=True), mp4_container(to_end=True)):
            self.assertEqual(normalize_media(PreparedComponent("video", data=data), caps()).kind, "video")
        for data in (mp4_container(brand=b"qt  "), mp4_container(brand=b"avif"),
                     mp4_container(brand=b"M4A ", handler=b"soun"),
                     mp4_container(handler=b"soun"), box(b"ftyp", b"isom" + b"\0" * 4)):
            with self.subTest(data=data[:20]):
                self.assertEqual(normalize_media(PreparedComponent("video", data=data), caps()).kind, "file")

    async def test_malformed_mp4_boxes_downgrade_instead_of_uploading_native_video(self):
        valid = mp4_container()
        invalid = (valid[:-1], b"\0\0\0\1ftyp", b"\0\0\0\4ftyp",
                   b"\0\0\0\1ftyp" + (2**64 - 1).to_bytes(8, "big"),
                   box(b"ftyp", b"isom" + b"\0" * 4) + box(b"moov", b"\0\0\0\4trak"),
                   box(b"ftyp", b"isom" + b"\0" * 4) + box(b"mdat", b"vide"))
        for data in invalid:
            with self.subTest(data=data[:20]):
                self.assertEqual(normalize_media(PreparedComponent("video", data=data), caps()).kind, "file")

    async def test_native_oversize_downgrades_but_file_cap_rejects(self):
        item = PreparedComponent("audio", data=b"OggSOpusHead"+b"a"*1100, requested_kind="audio")
        self.assertEqual(normalize_media(item, caps()).kind, "file")
        with self.assertRaises(DeliveryError) as caught:
            normalize_media(replace(item, data=b"a"*2001), caps())
        self.assertEqual(caught.exception.code, "file_too_large")
        with self.assertRaises(DeliveryError):
            normalize_media(PreparedComponent("text", text="中"*34), caps())

    async def test_batch_size_and_empty_file_rejected(self):
        deps = context().deps; deps.session_file_artifacts={"a": {}, "b": {}}
        deps.attachment_by_id = {key: AttachmentRef(id=key, name="test.txt") for key in ("a", "b")}
        deps.file_store = SimpleNamespace(download_bounded=AsyncMock(return_value=b"1"*60))
        with patch("deep_agent.channels.message_tool.max_upload_bytes", return_value=100):
            with self.assertRaises(DeliveryError) as caught:
                await prepare_components(deps, [MessageComponent(type="file", file_id=k) for k in ("a", "b")], caps())
        self.assertEqual(caught.exception.code, "batch_too_large")
        with self.assertRaises(DeliveryError):
            normalize_media(PreparedComponent("file", data=b""), caps())

    async def test_late_invalid_component_prevents_entire_gateway_send(self):
        ctx = context(); gateway = SimpleNamespace(send_message=AsyncMock())
        toolset = build_channel_message_toolset(None, gateway, source(), caps())
        function = toolset.tools["channel_message"].function
        with patch("deep_agent.channels.message_tool.resolve_run_context", AsyncMock(return_value=source())):
            with self.assertRaises(ModelRetry):
                await function(ctx, [MessageComponent(type="text", text="first"), MessageComponent(type="file", file_id="foreign")])
        gateway.send_message.assert_not_awaited()

    async def test_context_mismatch_and_changed_source_block_sending(self):
        gateway = SimpleNamespace(send_message=AsyncMock())
        function = build_channel_message_toolset(None, gateway, source(), caps()).tools["channel_message"].function
        ctx = context(); ctx.deps.agent_id = "other"
        with self.assertRaises(ModelRetry):
            await function(ctx, [MessageComponent(type="text", text="x")])
        ctx.deps.agent_id = "agent"
        with patch("deep_agent.channels.message_tool.resolve_run_context", AsyncMock(return_value=replace(source(), session_id="another"))):
            with self.assertRaises(ModelRetry):
                await function(ctx, [MessageComponent(type="text", text="x")])
        gateway.send_message.assert_not_awaited()
