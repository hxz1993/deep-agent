"""Outbound media over the existing SDK connection, using only public methods.

Protocol: WecomTeam/aibot-node-sdk src/client.ts uploadMedia. This module
owns no socket, credentials, model routing, or shared stream reply handle.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from typing import Any

from aibot import generate_req_id

from deep_agent.channels.outbound import ComponentReceipt, DeliveryError, MessageCapabilities, PreparedComponent

CHUNK_SIZE = 512 * 1024
MAX_CHUNKS = 100
CHUNK_RETRIES = 2
RETRY_DELAY = 0.5
# SDK 1.0.2 exposes negative ACKs as RuntimeError rather than a typed error.
_NEGATIVE_ACK = re.compile(r"^Reply ack error: errcode=-?\d+, errmsg=")


def message_capabilities() -> MessageCapabilities:
    return MessageCapabilities(
        kinds=("text", "image", "file", "audio", "video"), supports_reply=False,
        text_limit=20480, file_limit=20 * 1024 * 1024,
        image_limit=10 * 1024 * 1024, audio_limit=2 * 1024 * 1024,
        video_limit=10 * 1024 * 1024, audio_formats=("amr",),
        # The public bot SDK documents JPEG/PNG uploads. Other detected image
        # containers remain sendable as files, not assumed native images.
        image_formats=("png", "jpeg"),
    )


def _validate_ack(ack: Any, *, sending: bool = False) -> dict[str, Any]:
    if not isinstance(ack, dict) or "errcode" not in ack:
        raise DeliveryError("invalid_receipt", "企微未返回有效回执", unknown=sending)
    if ack["errcode"] != 0:
        raise DeliveryError("platform_rejected", "企微拒绝了本次请求，请检查权限、格式或发送限制")
    return ack


async def _upload_request(client: Any, command: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        ack = await client.reply(
            {"headers": {"req_id": generate_req_id(command)}}, body, cmd=command,
        )
    except Exception as exc:
        if isinstance(exc, RuntimeError) and _NEGATIVE_ACK.match(str(exc)):
            raise DeliveryError("platform_rejected", "企微拒绝了本次上传，请检查权限、格式或上传限制") from None
        raise DeliveryError("upload_failed", "企微媒体上传失败，尚未发送消息") from None
    return _validate_ack(ack)


async def upload_media(client: Any, data: bytes, *, media_type: str, filename: str) -> str:
    """Bounded, zero-based chunk upload; retry chunks only, never message sends."""
    if not data or len(data) > CHUNK_SIZE * MAX_CHUNKS:
        raise DeliveryError("invalid_media_size", "企微媒体不能为空，且不能超过 50 MiB")
    chunks = (len(data) + CHUNK_SIZE - 1) // CHUNK_SIZE
    init = await _upload_request(client, "aibot_upload_media_init", {
        "type": media_type, "filename": filename, "total_size": len(data),
        "total_chunks": chunks, "md5": hashlib.md5(data).hexdigest(),
    })
    upload_id = (init.get("body") or {}).get("upload_id")
    if not isinstance(upload_id, str) or not upload_id:
        raise DeliveryError("invalid_upload_receipt", "企微未返回上传凭据，尚未发送消息")
    # Sequential chunks leave room for existing streaming/heartbeat traffic and
    # avoid untracked worker tasks when a run is cancelled.
    for index in range(chunks):
        body = {
            "upload_id": upload_id, "chunk_index": index,
            "base64_data": base64.b64encode(data[index * CHUNK_SIZE:(index + 1) * CHUNK_SIZE]).decode("ascii"),
        }
        for attempt in range(CHUNK_RETRIES + 1):
            try:
                await _upload_request(client, "aibot_upload_media_chunk", body)
                break
            except DeliveryError as exc:
                # Explicit permission/format rejection must not be hidden by retry.
                if exc.code == "platform_rejected" or attempt == CHUNK_RETRIES:
                    raise
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))
    finished = await _upload_request(client, "aibot_upload_media_finish", {"upload_id": upload_id})
    media_id = (finished.get("body") or {}).get("media_id")
    if not isinstance(media_id, str) or not media_id:
        raise DeliveryError("invalid_upload_receipt", "企微未返回媒体凭据，尚未发送消息")
    return media_id


async def send_component(client: Any, peer_id: str, component: PreparedComponent) -> ComponentReceipt:
    if component.kind == "text":
        body = {"msgtype": "markdown", "markdown": {"content": component.text}}
    elif component.kind in {"image", "file", "audio", "video"}:
        native_kind = "voice" if component.kind == "audio" else component.kind
        media_id = await upload_media(client, component.data or b"", media_type=native_kind, filename=component.filename)
        body = {"msgtype": native_kind, native_kind: {"media_id": media_id}}
    else:
        raise DeliveryError("unsupported_content", "企微不支持此消息类型")
    try:
        ack = await client.send_message(peer_id, body)
    except Exception as exc:
        if isinstance(exc, RuntimeError) and _NEGATIVE_ACK.match(str(exc)):
            raise DeliveryError("platform_rejected", "企微拒绝了本次发送，请检查权限、格式或发送限制") from None
        # Disconnects, SDK timeouts and unexpected failures after dispatch cannot
        # prove non-delivery. Never retry these sends automatically.
        raise DeliveryError("delivery_unknown", "企微发送结果未知，请先在聊天中确认，勿重复发送", unknown=True) from None
    ack = _validate_ack(ack, sending=True)
    request_id = (ack.get("headers") or {}).get("req_id")
    return ComponentReceipt(kind=component.kind, request_id=request_id if isinstance(request_id, str) else None)
