"""Current-conversation message tool; routing is never supplied by the model."""
from __future__ import annotations

import asyncio
import io
import mimetypes
from pathlib import PurePosixPath
from typing import Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.toolsets import FunctionToolset

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.media_pipeline import max_upload_bytes
from deep_agent.files.storage import FileStoreError
from deep_agent.channels.outbound import (
    ChannelRunContext, DeliveryError, MessageCapabilities, PreparedComponent, resolve_run_context,
)


class MessageComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text", "image", "file", "audio", "video"]
    text: str | None = Field(default=None, max_length=20000)
    file_id: str | None = Field(default=None, max_length=256)
    display_name: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def valid_content(self):
        if self.type == "text":
            if not self.text or not self.text.strip() or self.file_id or self.display_name:
                raise ValueError("text组件仅填写非空text")
        elif not self.file_id or not self.file_id.strip() or self.text is not None:
            raise ValueError("媒体组件填写当前会话file_id，文字使用独立text组件")
        return self


def _is_mp4_video(data: bytes) -> bool:
    """Recognize an MP4 container with a video track, without decoding codecs.

    `ftyp` is shared by QuickTime, AVIF and audio-only M4A. A matching byte
    sequence alone is not enough to select a native video upload.
    """
    remaining_boxes = 4096

    def boxes(start: int, end: int):
        nonlocal remaining_boxes
        while start < end:
            remaining_boxes -= 1
            if remaining_boxes < 0 or end - start < 8:
                raise ValueError("Invalid BMFF box")
            size = int.from_bytes(data[start:start + 4], "big")
            kind = data[start + 4:start + 8]
            header = 8
            if size == 1:
                if end - start < 16:
                    raise ValueError("Invalid extended BMFF box")
                size = int.from_bytes(data[start + 8:start + 16], "big")
                header = 16
            elif size == 0:
                size = end - start
            if size < header or size > end - start:
                raise ValueError("Invalid BMFF box bounds")
            yield kind, start + header, start + size
            start += size

    mp4_brands = {b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"iso7",
                  b"iso8", b"iso9", b"mp41", b"mp42", b"avc1", b"dash", b"M4V "}
    has_mp4_brand = False
    has_video_track = False
    try:
        for kind, start, end in boxes(0, len(data)):
            if kind == b"ftyp":
                if end - start < 8 or (end - start - 8) % 4:
                    return False
                major = data[start:start + 4]
                if major in {b"qt  ", b"avif", b"avis", b"heic", b"heix", b"mif1", b"msf1"}:
                    return False
                brands = {major, *(data[i:i + 4] for i in range(start + 8, end, 4))}
                has_mp4_brand = bool(brands & mp4_brands)
            elif kind == b"moov":
                for child, track_start, track_end in boxes(start, end):
                    if child != b"trak":
                        continue
                    for track_child, media_start, media_end in boxes(track_start, track_end):
                        if track_child != b"mdia":
                            continue
                        for media_child, handler_start, handler_end in boxes(media_start, media_end):
                            if media_child == b"hdlr" and handler_end - handler_start >= 12:
                                # FullBox flags/version, pre_defined, handler_type.
                                has_video_track |= data[handler_start + 8:handler_start + 12] == b"vide"
    except ValueError:
        return False
    return has_mp4_brand and has_video_track


def _media_format(data: bytes, kind: str) -> str:
    if kind == "image":
        try:
            with Image.open(io.BytesIO(data)) as picture:
                fmt = (picture.format or "").lower()
                picture.verify()
                return fmt
        except Exception:
            return ""
    if kind == "audio":
        if data.startswith(b"#!AMR\n"):
            return "amr"
        if data.startswith(b"OggS") and b"OpusHead" in data[:512]:
            return "opus"
    if kind == "video" and _is_mp4_video(data):
        return "mp4"
    return ""


def normalize_media(component: PreparedComponent, caps: MessageCapabilities) -> PreparedComponent:
    """Conservative format detection; unsupported native media remains a file."""
    from dataclasses import replace

    if component.kind == "text":
        if not component.text.strip() or len(component.text.encode("utf-8")) > caps.text_limit:
            raise DeliveryError("invalid_text", f"文字不能为空或超过{caps.text_limit}字节，请拆分发送")
        return component
    data = component.data or b""
    if not data:
        raise DeliveryError("empty_file", "不能发送空文件")
    kind = component.kind
    if kind != "file":
        fmt = _media_format(data, kind)
        formats = getattr(caps, f"{kind}_formats", ())
        limit = getattr(caps, f"{kind}_limit", 0)
        if not fmt or fmt not in formats or len(data) > limit:
            kind = "file"
    if kind not in caps.kinds:
        raise DeliveryError("unsupported_content", "当前渠道不支持此消息类型")
    limit = min(getattr(caps, f"{kind}_limit"), max_upload_bytes())
    if len(data) > limit:
        raise DeliveryError("file_too_large", f"文件超过当前渠道{limit}字节上限")
    return replace(component, kind=kind)


async def prepare_components(deps: AgentDeps, components: list[MessageComponent],
                             caps: MessageCapabilities) -> list[PreparedComponent]:
    if not 1 <= len(components) <= 10:
        raise DeliveryError("invalid_components", "每次发送1至10个内容组件")
    prepared = []
    total_bytes = 0
    for component in components:
        if component.type == "text":
            item = PreparedComponent("text", text=component.text or "", requested_kind="text")
        else:
            file_id = component.file_id or ""
            attachment = deps.attachment_by_id.get(file_id)
            artifact = deps.session_file_artifacts.get(file_id) or deps.tool_output_artifacts.get(file_id)
            if attachment is None and artifact is None:
                raise DeliveryError("file_not_authorized", "file_id不属于当前会话的附件或生成产物")
            if deps.file_store is None:
                raise DeliveryError("files_unavailable", "文件服务不可用")
            filename = (attachment.name if attachment else artifact.get("file_name")) or "attachment"
            filename = component.display_name or filename
            filename = PurePosixPath(filename.replace("\\", "/")).name
            if not filename or filename in {".", ".."} or any(ord(c) < 32 for c in filename):
                raise DeliveryError("invalid_filename", "文件名无效")
            try:
                data = await deps.file_store.download_bounded(file_id, deps.request_headers,
                    max_bytes=min(caps.file_limit, max_upload_bytes() - total_bytes))
            except FileStoreError:
                raise DeliveryError("file_unavailable", "文件无法读取或超过发送大小上限，本次尚未发送") from None
            total_bytes += len(data)
            if total_bytes > max_upload_bytes():
                raise DeliveryError("batch_too_large", "本次附件总大小超过上传上限，请分次发送")
            mime = (attachment.mime if attachment else artifact.get("mime")) or mimetypes.guess_type(filename)[0]
            item = PreparedComponent(component.type, data=data, filename=filename,
                                     mime=mime or "application/octet-stream", requested_kind=component.type)
        prepared.append(normalize_media(item, caps))
    return prepared


def build_channel_message_toolset(store, gateway, source: ChannelRunContext, caps: MessageCapabilities):
    async def send(ctx: RunContext[AgentDeps], components: list[MessageComponent], reply: bool):
        deps = ctx.deps
        if deps.cancellation_token is not None and deps.cancellation_token.cancelled:
            raise asyncio.CancelledError
        if (deps.session_id, deps.agent_id, deps.user) != (source.session_id, source.agent_id, source.user_id):
            raise ModelRetry("当前渠道上下文与运行不匹配")
        try:
            current = await resolve_run_context(store, account_id=source.account_id,
                channel_run_id=source.channel_run_id, session_id=source.session_id,
                agent_id=source.agent_id, user_id=source.user_id)
            if current != source:
                raise DeliveryError("source_changed", "渠道目标已变更")
            items = await prepare_components(deps, components, caps)
            if not deps.run_id or not ctx.tool_call_id:
                raise DeliveryError("run_unavailable", "缺少有效运行和工具调用标识")
            return await gateway.send_message(source, deps.run_id, ctx.tool_call_id, items, reply=reply)
        except DeliveryError as exc:
            # Preflight errors are safe for the model to correct. Delivery
            # failures/unknown outcomes are returned by the gateway, not retried.
            if exc.unknown:
                return {"status": "unknown", "error": str(exc)}
            raise ModelRetry(str(exc)) from None
        except asyncio.CancelledError:
            raise
        except Exception:
            return {"status": "unknown", "error": "发送或文件服务暂不可用，请勿盲目重复发送"}

    if caps.supports_reply:
        async def channel_message(ctx: RunContext[AgentDeps], components: list[MessageComponent],
                                  action: Literal["send"] = "send", reply_to: Literal["current"] | None = None) -> dict:
            """Send ordered text/image/file/audio/video to this conversation only. Media requires an authorized file_id. Use reply_to=current to quote this turn's incoming message. Unsupported native media is sent as a file. Normal answers still stream automatically; do not repeat successfully sent content."""
            return await send(ctx, components, reply_to == "current")
    else:
        async def channel_message(ctx: RunContext[AgentDeps], components: list[MessageComponent],
                                  action: Literal["send"] = "send") -> dict:
            """Send ordered text/image/file/audio/video to this conversation only. Media requires an authorized file_id. Unsupported native media is sent as a file. This channel does not support native message quotes. Normal answers still stream automatically; do not repeat successfully sent content."""
            return await send(ctx, components, False)
    return FunctionToolset([channel_message], id="runtime:channel")
