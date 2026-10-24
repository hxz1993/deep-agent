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

from deep_agent.channels.media_formats import is_mp4_video as _is_mp4_video
from deep_agent.channels.message_payloads import TextCardPayload, NewsPayload, validate_message_payload
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.media_pipeline import max_upload_bytes
from deep_agent.files.storage import FileStoreError
from deep_agent.files.public_download import load_public_file
from deep_agent.channels.outbound import (
    ChannelRunContext, DeliveryError, MessageCapabilities, PreparedComponent, resolve_run_context,
)


class MessageComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text", "image", "file", "audio", "video", "textcard", "news"]
    text: str | None = Field(default=None, max_length=20000)
    file_id: str | None = Field(default=None, max_length=256)
    url: str | None = Field(default=None, max_length=8192)
    display_name: str | None = Field(default=None, max_length=200)

    payload: TextCardPayload | NewsPayload | None = None

    @model_validator(mode="after")
    def valid_content(self):
        if self.type in {"textcard", "news"}:
            if self.text is not None or self.file_id is not None or self.url is not None or self.display_name is not None:
                raise ValueError("卡片组件仅填写payload")
            validate_message_payload(self.type, self.payload)
            return self
        if self.payload is not None:
            raise ValueError("此消息类型不支持payload")
        if self.type == "text":
            if not self.text or not self.text.strip() or self.file_id is not None or self.url is not None or self.display_name:
                raise ValueError("text组件仅填写非空text")
        elif self.text is not None or ((self.file_id is None) == (self.url is None)):
            raise ValueError("媒体组件填写当前会话file_id或公开url，二选一；文字使用独立text组件")
        elif not (self.file_id if self.file_id is not None else self.url).strip():
            raise ValueError("媒体来源不能为空")
        return self



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

    _check_capability(component.kind, caps)
    if component.kind in {"textcard", "news"}:
        if component.text or component.data is not None or component.filename:
            raise DeliveryError("invalid_content", "卡片仅允许结构化内容")
        try:
            return replace(component, payload=validate_message_payload(component.kind, component.payload))
        except ValueError:
            raise DeliveryError("invalid_content", "卡片内容或链接无效") from None
    if component.payload is not None:
        raise DeliveryError("invalid_content", "此消息类型不支持结构化内容")
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
        if kind not in caps.kinds or not fmt or fmt not in formats or len(data) > limit:
            if not caps.media_fallback_to_file:
                raise DeliveryError("unsupported_media", "当前格式或大小不适合原生媒体发送；如需按文件发送，请显式选择 file")
            kind = "file"
    if kind not in caps.kinds:
        raise DeliveryError("unsupported_content", "当前渠道不支持此消息类型")
    limit = min(getattr(caps, f"{kind}_limit"), max_upload_bytes())
    if len(data) > limit:
        raise DeliveryError("file_too_large", f"文件超过当前渠道{limit}字节上限")
    return replace(component, kind=kind)


def _check_capability(kind: str, caps: MessageCapabilities) -> None:
    native = kind in caps.kinds and (kind in {"text", "textcard", "news"} or getattr(caps, f"{kind}_limit", 0) > 0)
    fallback = kind in {"image", "audio", "video"} and caps.media_fallback_to_file and "file" in caps.kinds and caps.file_limit > 0
    if not native and not fallback:
        raise DeliveryError("unsupported_content", "当前渠道不支持此消息类型")


async def prepare_components(deps: AgentDeps, components: list[MessageComponent],
                             caps: MessageCapabilities) -> list[PreparedComponent]:
    if not 1 <= len(components) <= 10:
        raise DeliveryError("invalid_components", "每次发送1至10个内容组件")
    prepared = []
    total_bytes = 0
    for component in components:
        _check_capability(component.type, caps)
        if component.type in {"textcard", "news"}:
            item = PreparedComponent(component.type, requested_kind=component.type,
                                     payload=validate_message_payload(component.type, component.payload))
        elif component.type == "text":
            item = PreparedComponent("text", text=component.text or "", requested_kind="text")
        elif component.url is not None:
            native_limit = getattr(caps, f"{component.type}_limit", 0) if component.type in caps.kinds else 0
            fallback_limit = caps.file_limit if caps.media_fallback_to_file and "file" in caps.kinds else 0
            data, filename, mime, _ = await load_public_file(
                component.url, max_bytes=min(max(native_limit, fallback_limit), max_upload_bytes() - total_bytes),
            )
            total_bytes += len(data)
            if total_bytes > max_upload_bytes():
                raise DeliveryError("batch_too_large", "本次附件总大小超过上传上限，请分次发送")
            filename = PurePosixPath((component.display_name or filename).replace("\\", "/")).name
            if not filename or filename in {".", ".."} or any(ord(c) < 32 for c in filename):
                raise DeliveryError("invalid_filename", "文件名无效")
            item = PreparedComponent(component.type, data=data, filename=filename[:200],
                                     mime=mime, requested_kind=component.type)
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
            native_limit = getattr(caps, f"{component.type}_limit", 0) if component.type in caps.kinds else 0
            fallback_limit = caps.file_limit if caps.media_fallback_to_file and "file" in caps.kinds else 0
            download_limit = max(native_limit, fallback_limit)
            try:
                data = await deps.file_store.download_bounded(file_id, deps.request_headers,
                    max_bytes=min(download_limit, max_upload_bytes() - total_bytes))
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
    send_lock = asyncio.Lock()

    async def send(ctx: RunContext[AgentDeps], components: list[MessageComponent], reply: bool):
        # Serialize sends from native batches and PTC gather alike. This lock is
        # local to the trusted run's toolset, not a global receipt/dedup store.
        async with send_lock:
            return await send_ordered(ctx, components, reply)

    async def send_ordered(ctx: RunContext[AgentDeps], components: list[MessageComponent], reply: bool):
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
            if deps.cancellation_token is not None and deps.cancellation_token.cancelled:
                raise asyncio.CancelledError
            if not deps.run_id:
                raise DeliveryError("run_unavailable", "缺少有效运行标识")
            return await gateway.send_message(source, deps.run_id, items, reply=reply)
        except DeliveryError as exc:
            # Preflight errors are safe for the model to correct. Delivery
            # failures/unknown outcomes are returned by the gateway, not retried.
            if exc.unknown:
                return {"status": "unknown", "error": str(exc)}
            raise ModelRetry(str(exc)) from None
        except ModelRetry:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            return {"status": "unknown", "error": "发送或文件服务暂不可用，请勿盲目重复发送"}

    if caps.supports_reply:
        async def channel_message(ctx: RunContext[AgentDeps], components: list[MessageComponent],
                                  action: Literal["send"] = "send", reply_to: Literal["current"] | None = None) -> dict:
            """Send ordered content to this conversation only. Media accepts exactly one authorized file_id or public HTTP(S) url; known URLs need no Python or platform upload. Downloads stay in call-local memory. Cards use payload: textcard needs title/description/url, news needs 1-8 articles. Use reply_to=current to quote this turn. Unsupported native media is sent as a file. Do not repeat successful or unconfirmed sends; interruption after dispatch may leave delivery unknown. Normal answers are delivered automatically."""
            return await send(ctx, components, reply_to == "current")
    else:
        async def channel_message(ctx: RunContext[AgentDeps], components: list[MessageComponent],
                                  action: Literal["send"] = "send") -> dict:
            """Send ordered content to this conversation only. Media accepts exactly one authorized file_id or public HTTP(S) url; known URLs need no Python or platform upload. Downloads stay in call-local memory. Cards use payload: textcard needs title/description/url, news needs 1-8 articles. This channel does not support native message quotes. Unsupported native media is sent as a file. Do not repeat successful or unconfirmed sends; interruption after dispatch may leave delivery unknown. Normal answers are delivered automatically."""
            return await send(ctx, components, False)
    return FunctionToolset([channel_message], id="runtime:channel")
