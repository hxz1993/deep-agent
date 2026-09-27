"""Provider-neutral outbound contracts. No model-controlled routing or credentials."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json


@dataclass(frozen=True)
class MessageTarget:
    account_id: str
    peer_type: str
    peer_id: str
    message_id: str
    thread_id: str | None = None
    chat_id: str | None = None


@dataclass(frozen=True)
class PreparedComponent:
    kind: str
    text: str = ""
    data: bytes | None = None
    filename: str = ""
    mime: str = "application/octet-stream"
    requested_kind: str = ""


@dataclass(frozen=True)
class ComponentReceipt:
    kind: str
    message_id: str | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class MessageCapabilities:
    kinds: tuple[str, ...]
    supports_reply: bool
    text_limit: int
    file_limit: int
    image_limit: int
    audio_limit: int
    video_limit: int
    audio_formats: tuple[str, ...] = ()
    video_formats: tuple[str, ...] = ("mp4",)
    image_formats: tuple[str, ...] = ("png", "jpeg", "gif", "webp", "bmp")


class DeliveryError(Exception):
    """Safe public error; never construct its message from raw SDK exceptions."""

    def __init__(self, code: str, message: str, unknown: bool = False):
        super().__init__(message)
        self.code = code
        self.unknown = unknown


@dataclass(frozen=True)
class ChannelRunContext:
    account_id: str
    channel_run_id: str
    session_id: str
    agent_id: str
    user_id: str
    target: MessageTarget


async def resolve_run_context(store, *, account_id: str, channel_run_id: str,
                              session_id: str, agent_id: str, user_id: str) -> ChannelRunContext:
    account = await store.get_account(account_id)
    run = await store.get_run(channel_run_id)
    binding = await store.get_binding_by_id(run.binding_id) if run else None
    if (account is None or not account.enabled or account.agent_id != agent_id
            or account.session_user_id != user_id or run is None
            or run.account_id != account_id or run.session_id != session_id
            or run.processing_status != "running" or binding is None
            or binding.account_id != account_id or binding.session_id != session_id):
        raise DeliveryError("source_unavailable", "当前渠道会话已结束、停用或变更")
    return ChannelRunContext(account_id, channel_run_id, session_id, agent_id, user_id,
                             MessageTarget(account_id, binding.peer_type, binding.peer_id,
                                           run.external_message_id, run.thread_id,
                                           (run.source_metadata or {}).get("lark_chat_id")))


def component_hash(component: PreparedComponent) -> str:
    content = {"kind": component.kind, "text": component.text, "filename": component.filename,
               "mime": component.mime, "requested_kind": component.requested_kind,
               "sha256": hashlib.sha256(component.data or b"").hexdigest()}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def request_hash(components: list[PreparedComponent], reply: bool) -> str:
    return hashlib.sha256(json.dumps([reply, [component_hash(c) for c in components]]).encode()).hexdigest()
