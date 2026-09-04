from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class InboundMedia:
    kind: str
    filename: str
    mime: str
    handle: object = field(repr=False)


@dataclass(frozen=True)
class InboundQuote:
    """Quoted content, separate from the sender's current instruction."""
    message_type: str
    text: str
    media: tuple[InboundMedia, ...] = ()


@dataclass(frozen=True)
class InboundMessage:
    message_type: str
    message_id: str
    request_id: str | None
    sender_id: str
    peer_type: str
    peer_id: str
    peer_metadata: dict[str, str]
    text: str
    media: tuple[InboundMedia, ...] = ()
    payload_hash: str = ""
    reply_handle: object = field(default=None, repr=False)
    command_eligible: bool = True
    quote: InboundQuote | None = None

    @property
    def prompt(self) -> str:
        if self.text.strip():
            return self.text.strip()
        if self.quote is not None:
            return "请结合引用消息回复。"
        labels = {"image": "图片", "file": "文件", "voice": "语音", "video": "视频"}
        kinds = "、".join(labels.get(item.kind, "附件") for item in self.media)
        return f"请查看我发送的{kinds or '附件'}。"


@dataclass(frozen=True)
class AdapterStatus:
    state: str
    message: str
    authenticated: bool
    updated_at: datetime


MessageCallback = Callable[[InboundMessage], Awaitable[None]]


class ChannelAdapter(Protocol):
    async def start(self, on_message: MessageCallback) -> None: ...
    async def stop(self) -> None: ...
    async def test_connection(self, timeout: float) -> dict[str, Any]: ...
    def status(self) -> dict[str, Any]: ...
    async def download_media(self, media: InboundMedia) -> tuple[bytes, str | None]: ...
    def new_reply_id(self) -> str: ...
    async def reply(self, message: InboundMessage, reply_id: str, content: str, finish: bool) -> None: ...
