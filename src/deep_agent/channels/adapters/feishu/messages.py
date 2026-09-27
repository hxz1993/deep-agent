"""Map official SDK messages to the shared channel contract (no wire protocol)."""
from __future__ import annotations

import hashlib
import json
import mimetypes
from dataclasses import dataclass
from typing import Any

from deep_agent.channels.contracts import InboundMedia, InboundMessage, InboundQuote


class UnsupportedMessage(ValueError):
    """A non-conversational message should not start a model run."""


@dataclass(frozen=True)
class MediaHandle:
    message_id: str
    file_key: str
    resource_type: str


@dataclass(frozen=True)
class ReplyHandle:
    message_id: str
    chat_id: str
    thread_id: str | None = None


def media_from_message(message: Any) -> tuple[InboundMedia, ...]:
    media = []
    for resource in message.resources:
        if resource.type not in {"image", "file", "audio", "video"} or not resource.file_key:
            continue
        extension, mime = {"image": ("jpg", "image/jpeg"), "audio": ("ogg", "audio/ogg"),
                           "video": ("mp4", "video/mp4"), "file": ("bin", "application/octet-stream")}[resource.type]
        filename = resource.file_name or f"feishu-{resource.type}.{extension}"
        media.append(InboundMedia(
            kind="voice" if resource.type == "audio" else resource.type,
            filename=filename, mime=mimetypes.guess_type(filename)[0] or mime,
            handle=MediaHandle(message.message_id, resource.file_key,
                               "image" if resource.type == "image" else "file"),
        ))
    return tuple(media)


def parse_message(message: Any, *, envelope: dict | None = None, quote: Any = None) -> InboundMessage:
    conversation, sender = message.conversation, message.sender
    if message.content.kind not in {"text", "post", "image", "file", "audio", "video"}:
        raise UnsupportedMessage("Unsupported Feishu message content")
    if not message.message_id or not conversation.chat_id or not sender.open_id:
        raise ValueError("Feishu message has no stable identity")
    if conversation.chat_type not in {"p2p", "group", "topic"}:
        raise ValueError("Unknown Feishu conversation type")
    envelope = envelope or {}
    group = conversation.chat_type != "p2p"
    metadata = {f"lark_{key}": value for key, value in {
        "user_id": sender.user_id, "union_id": sender.union_id,
        "chat_id": conversation.chat_id, "chat_type": conversation.chat_type,
        "parent_id": message.reply_to_message_id or message.raw.get("parent_id"),
        "root_id": message.raw.get("root_id"), "sender_type": sender.sender_type,
    }.items() if value}
    quoted = None
    if quote is not None:
        # A quotation is context, never the sender's current instruction.
        quoted = InboundQuote(quote.raw_content_type or "text", quote.content_text,
                              media_from_message(quote))
    media = media_from_message(message)
    text = message.body_text or message.content_text
    if not text.strip() and not media and quoted is None:
        raise UnsupportedMessage("Empty Feishu message")
    canonical = {"id": message.message_id, "chat": conversation.chat_id,
                 "sender": sender.open_id, "raw": message.raw,
                 "text": message.content_text}
    return InboundMessage(
        message_type=message.raw_content_type or message.content.kind,
        message_id=message.message_id, request_id=envelope.get("event_id"),
        sender_id=sender.open_id, peer_type="group" if group else "single",
        peer_id=conversation.chat_id if group else sender.open_id,
        peer_metadata={"chat_id": conversation.chat_id, **({"display_name": sender.display_name}
                       if not group and sender.display_name else {})},
        sender_name=sender.display_name, tenant_id=envelope.get("tenant_key"),
        thread_id=conversation.thread_id, source_metadata=metadata,
        text=text, media=media,
        payload_hash=hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                                              default=str).encode()).hexdigest(),
        reply_handle=ReplyHandle(message.message_id, conversation.chat_id, conversation.thread_id),
        command_eligible=message.content.kind == "text", quote=quoted,
    )
