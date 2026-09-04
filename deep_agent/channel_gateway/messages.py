from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


MEDIA_MIMES = {
    "image": "image/jpeg",
    "file": "application/octet-stream",
    "voice": "audio/amr",
    "video": "video/mp4",
}


@dataclass(frozen=True)
class InboundMedia:
    kind: str
    url: str
    aes_key: str
    filename: str
    mime: str


@dataclass(frozen=True)
class InboundMessage:
    frame: dict[str, Any]
    message_type: str
    message_id: str
    request_id: str | None
    sender_id: str
    peer_type: str
    peer_id: str
    peer_display_name: str | None
    text: str
    media: tuple[InboundMedia, ...] = field(default_factory=tuple)
    payload_hash: str = ""

    @property
    def prompt(self) -> str:
        if self.text.strip():
            return self.text.strip()
        labels = {
            "image": "图片",
            "file": "文件",
            "voice": "语音",
            "video": "视频",
        }
        kinds = "、".join(labels.get(item.kind, "附件") for item in self.media)
        return f"请查看我发送的{kinds or '附件'}。"


def parse_message(frame: dict[str, Any]) -> InboundMessage:
    body = _mapping(frame.get("body"))
    headers = _mapping(frame.get("headers"))
    message_type = str(body.get("msgtype") or body.get("msg_type") or "").lower()
    sender = _mapping(body.get("from"))
    sender_id = str(
        sender.get("userid")
        or sender.get("user_id")
        or body.get("from_userid")
        or ""
    ).strip()
    if not sender_id:
        raise ValueError("WeCom message does not contain sender userid")
    chat_id = str(body.get("chatid") or body.get("chat_id") or "").strip()
    chat_type = str(body.get("chattype") or ("group" if chat_id else "single"))
    if chat_type not in {"single", "group"} or (chat_type == "group" and not chat_id):
        raise ValueError("WeCom message has an invalid chattype/chatid")
    # Persist the upstream ID field name, not another conversation-type enum.
    peer_type = "chatid" if chat_type == "group" else "userid"
    peer_id = chat_id if peer_type == "chatid" else sender_id
    message_id = str(
        body.get("msgid")
        or body.get("msg_id")
        or headers.get("req_id")
        or headers.get("request_id")
        or ""
    ).strip()
    if not message_id:
        raise ValueError("WeCom message does not contain msgid")
    request_id = str(headers.get("req_id") or headers.get("request_id") or "").strip() or None
    peer_display_name = str(
        (body.get("chat_name") if peer_type == "chatid" else
         sender.get("name") or sender.get("alias")) or ""
    ).strip() or None

    text_parts: list[str] = []
    media: list[InboundMedia] = []
    _collect_item(body, message_type, text_parts, media)
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return InboundMessage(
        frame=frame,
        message_type=message_type,
        message_id=message_id,
        request_id=request_id,
        sender_id=sender_id,
        peer_type=peer_type,
        peer_id=peer_id,
        peer_display_name=peer_display_name,
        text="\n".join(part for part in text_parts if part.strip()),
        media=tuple(media),
        payload_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _collect_item(
    item: dict[str, Any],
    message_type: str,
    text_parts: list[str],
    media: list[InboundMedia],
) -> None:
    if message_type == "text":
        content = _mapping(item.get("text")).get("content")
        if content is None:
            content = item.get("content")
        if content is not None:
            text_parts.append(str(content))
        return
    if message_type == "mixed":
        mixed = _mapping(item.get("mixed"))
        children = mixed.get("msg_item") or mixed.get("items") or item.get("msg_item") or []
        for child in children if isinstance(children, list) else []:
            child = _mapping(child)
            child_type = str(child.get("msgtype") or child.get("msg_type") or "").lower()
            _collect_item(child, child_type, text_parts, media)
        return
    if message_type in MEDIA_MIMES:
        payload = _mapping(item.get(message_type))
        if not payload:
            payload = item
        url = str(payload.get("url") or payload.get("download_url") or "").strip()
        aes_key = str(payload.get("aeskey") or payload.get("aes_key") or "").strip()
        if not url or not aes_key:
            raise ValueError(f"WeCom {message_type} message is missing encrypted media data")
        default_ext = {"image": "jpg", "file": "bin", "voice": "amr", "video": "mp4"}[message_type]
        filename = str(
            payload.get("filename")
            or payload.get("file_name")
            or f"wecom-{message_type}.{default_ext}"
        )
        media.append(
            InboundMedia(
                kind=message_type,
                url=url,
                aes_key=aes_key,
                filename=filename,
                mime=str(payload.get("mime") or payload.get("content_type") or MEDIA_MIMES[message_type]),
            )
        )
        return
    raise ValueError(f"Unsupported WeCom message type: {message_type or 'unknown'}")


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


__all__ = ["InboundMedia", "InboundMessage", "parse_message"]
