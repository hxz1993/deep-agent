from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from aibot import MessageType, WsFrame, WsFrameHeaders

from deep_agent.channels.contracts import InboundMedia, InboundMessage, InboundQuote


MEDIA_MIMES = {
    MessageType.Image.value: "image/jpeg",
    MessageType.File.value: "application/octet-stream",
    MessageType.Voice.value: "audio/amr",
    # SDK 1.0.2 forwards video through its generic message event.
    "video": "video/mp4",
}


@dataclass(frozen=True)
class MediaHandle:
    url: str = field(repr=False)
    aes_key: str = field(repr=False)


@dataclass(frozen=True)
class ReplyHandle:
    frame: WsFrameHeaders = field(repr=False)


def reply_handle(frame: WsFrame) -> ReplyHandle:
    """Keep only the SDK reply context, not the original message body."""
    headers = _mapping(frame.get("headers"))
    request_id = headers.get("req_id") or headers.get("request_id") or ""
    return ReplyHandle({"headers": {"req_id": str(request_id)}})


def parse_message(frame: WsFrame) -> InboundMessage:
    """Translate the native SDK callback into the common channel envelope."""
    body = _mapping(frame.get("body"))
    headers = _mapping(frame.get("headers"))
    message_type = str(body.get("msgtype") or body.get("msg_type") or "").lower()
    sender = _mapping(body.get("from"))
    sender_id = str(
        sender.get("userid") or sender.get("user_id") or body.get("from_userid") or ""
    ).strip()
    if not sender_id:
        raise ValueError("WeCom message does not contain sender userid")
    chat_id = str(body.get("chatid") or body.get("chat_id") or "").strip()
    chat_type = str(body.get("chattype") or ("group" if chat_id else "single"))
    if chat_type not in {"single", "group"} or (chat_type == "group" and not chat_id):
        raise ValueError("WeCom message has an invalid chattype/chatid")
    peer_type = chat_type
    peer_id = chat_id if peer_type == "group" else sender_id
    message_id = str(
        body.get("msgid") or body.get("msg_id")
        or headers.get("req_id") or headers.get("request_id") or ""
    ).strip()
    if not message_id:
        raise ValueError("WeCom message does not contain msgid")
    request_id = str(headers.get("req_id") or headers.get("request_id") or "").strip() or None
    display_name = str(
        (body.get("chat_name") if peer_type == "group" else
         sender.get("name") or sender.get("alias")) or ""
    ).strip() or None
    peer_metadata = {"chat_id": chat_id} if peer_type == "group" else {"user_id": sender_id}
    if chat_id:
        peer_metadata["chat_id"] = chat_id
    if display_name:
        peer_metadata["display_name"] = display_name

    text_parts: list[str] = []
    media: list[InboundMedia] = []
    _collect_item(body, message_type, text_parts, media)
    text = "\n".join(part for part in text_parts if part.strip())
    quote_body = _mapping(body.get("quote")) or _mapping(_mapping(body.get("text")).get("quote"))
    quote = None
    if quote_body:
        quote_type = str(quote_body.get("msgtype") or "").lower()
        quote_text: list[str] = []
        quote_media: list[InboundMedia] = []
        # The official quote uses the same content structures as a message.
        # Media handles stay private to the Adapter and use SDK download_file.
        _collect_item(quote_body, quote_type, quote_text, quote_media)
        quote = InboundQuote(quote_type, "\n".join(quote_text), tuple(quote_media))
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return InboundMessage(
        message_type=message_type,
        message_id=message_id,
        request_id=request_id,
        sender_id=sender_id,
        peer_type=peer_type,
        peer_id=peer_id,
        peer_metadata=peer_metadata,
        text=text,
        media=tuple(media),
        quote=quote,
        payload_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        reply_handle=reply_handle(frame),
        command_eligible=(
            message_type == MessageType.Text.value
            and not media
            and not body.get("quote")
            and not _mapping(body.get("text")).get("quote")
        ),
    )


def _collect_item(
    item: dict[str, Any], message_type: str,
    text_parts: list[str], media: list[InboundMedia],
) -> None:
    if message_type == MessageType.Text.value:
        content = _mapping(item.get("text")).get("content")
        if content is None:
            content = item.get("content")
        if content is not None:
            text_parts.append(str(content))
        return
    if message_type == MessageType.Mixed.value:
        mixed = _mapping(item.get("mixed"))
        children = mixed.get("msg_item") or mixed.get("items") or item.get("msg_item") or []
        for child in children if isinstance(children, list) else []:
            child = _mapping(child)
            child_type = str(child.get("msgtype") or child.get("msg_type") or "").lower()
            _collect_item(child, child_type, text_parts, media)
        return
    if message_type in MEDIA_MIMES:
        payload = _mapping(item.get(message_type)) or item
        url = str(payload.get("url") or payload.get("download_url") or "").strip()
        aes_key = str(payload.get("aeskey") or payload.get("aes_key") or "").strip()
        # WeCom may send native speech transcription without an audio attachment.
        if message_type == MessageType.Voice.value and payload.get("content"):
            text_parts.append(str(payload["content"]))
            if not url and not aes_key:
                return
        if not url or not aes_key:
            raise ValueError("WeCom message is missing encrypted media data")
        default_ext = {"image": "jpg", "file": "bin", "voice": "amr", "video": "mp4"}[message_type]
        filename = str(
            payload.get("filename") or payload.get("file_name")
            or f"wecom-{message_type}.{default_ext}"
        )
        media.append(InboundMedia(
            kind=message_type, filename=filename,
            mime=str(payload.get("mime") or payload.get("content_type") or MEDIA_MIMES[message_type]),
            handle=MediaHandle(url=url, aes_key=aes_key),
        ))
        return
    raise ValueError("Unsupported WeCom message type")


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


__all__ = ["InboundMedia", "InboundMessage", "MediaHandle", "ReplyHandle", "parse_message", "reply_handle"]
