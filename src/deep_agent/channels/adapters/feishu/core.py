"""Official Feishu Channel SDK adapter, using its public WebSocket lifecycle.

The SDK acknowledges an event when scheduling its handler, before our durable
inbox admission. A process crash in that short interval can lose an event; this
adapter does not claim exactly-once transport delivery. Business deduplication
remains owned by ChannelService and the persisted Feishu message ID.
"""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from lark_channel import FeishuChannel, LogLevel
from lark_channel.core import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest, RequestOption
from lark_channel.channel.config import (
    ChannelConfig, ChatQueueConfig, DedupConfig, InboundConfig, MediaCacheConfig,
    PolicyConfig, SafetyConfig, SecurityConfig, TextBatchConfig, TransportConfig,
)
from loguru import logger

from deep_agent.channels.contracts import InboundMedia, InboundMessage, MessageCallback
from .messages import MediaHandle, ReplyHandle, UnsupportedMessage, parse_message
from .sdk_logging import configure_sdk_logging


@dataclass
class ReplyState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    card_id: str | None = None
    sequence: int = 0
    static: bool = False
    sent: bool = False
    send_attempted: bool = False


class FeishuSDKAdapter:
    """One app connection; the shared gateway still owns all business execution."""
    max_pending = 128
    close_timeout = 12.0

    def __init__(self, account: Any, infinite_retry: bool = True) -> None:
        self.account = account
        self._infinite_retry = infinite_retry
        settings = (account.settings or {}).get("feishu") or {}
        policy = PolicyConfig(
            require_mention=True,
            group_policy="allowlist" if settings.get("group_allowlist") else "open",
            group_allowlist=settings.get("group_allowlist") or None,
        )
        configure_sdk_logging()
        self.client = FeishuChannel(config=ChannelConfig(
            app_id=account.bot_id, app_secret=account.secret, log_level=LogLevel.ERROR,
            transport=TransportConfig(kind="ws", auto_reconnect=infinite_retry,
                                      http_timeout_seconds=15, handshake_timeout_seconds=15),
            policy=policy,
            # Do not merge distinct platform messages: the durable inbox and
            # session serialization use each original message ID individually.
            safety=SafetyConfig(dedup=DedupConfig(enabled=False, ttl_seconds=0, max_entries=0),
                                text_batch=TextBatchConfig(delay_ms=0, long_delay_ms=0, max_messages=1),
                                chat_queue=ChatQueueConfig(enabled=False, merge_while_busy=False)),
            inbound=InboundConfig(emit_raw_events=True, expand_merge_forward=False,
                                  fetch_interactive_card=False),
            security=SecurityConfig(mode="strict", max_concurrent_ws_handlers=64,
                                    max_ws_fragment_bytes=8 * 1024 * 1024,
                                    max_ws_fragment_parts=128),
            media_cache=MediaCacheConfig(enabled=False),
            resolve_sender_names=True,
        ))
        self._state, self._message = "closed", "渠道已关闭"
        self._updated_at = datetime.now(timezone.utc)
        self._started = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._callback: MessageCallback | None = None
        self._connect_task: asyncio.Task | None = None
        self._pending: set[asyncio.Task] = set()
        self._slots = threading.BoundedSemaphore(self.max_pending)
        self._envelopes: OrderedDict[str, dict] = OrderedDict()
        self._replies: dict[str, ReplyState] = {}
        self.client.on("raw", self._capture_envelope)
        self.client.on("message", self._receive)
        self.client.on("reconnecting", lambda *_: self._set_state("reconnecting", "飞书连接中断，正在重连"))
        self.client.on("reconnected", lambda *_: self._set_state("connected", "已连接飞书"))
        self.client.on("error", lambda *_: logger.warning("Feishu SDK operation failed"))

    def _set_state(self, state: str, message: str) -> None:
        if not self._started and state in {"connected", "reconnecting"}:
            return
        self._state, self._message = state, message
        self._updated_at = datetime.now(timezone.utc)

    def status(self) -> dict[str, Any]:
        return {"state": self._state, "message": self._message,
                "authenticated": self._state == "connected",
                "updated_at": self._updated_at.isoformat()}

    async def start(self, on_message: MessageCallback) -> None:
        if self._started:
            return
        self._callback, self._loop = on_message, asyncio.get_running_loop()
        self._started = True
        self._set_state("connecting", "正在连接飞书")
        self._connect_task = asyncio.create_task(self._connect(), name="feishu-sdk-connect")

    async def _connect(self) -> None:
        delay = 1.0
        while self._started:
            try:
                await self.client.start_background(timeout=30)
                self._set_state("connected", "已连接飞书")
                return  # Established connections use the SDK's own reconnect loop.
            except asyncio.CancelledError:
                raise
            except Exception:
                self._set_state("failed", "飞书连接失败，请检查应用凭据、发布状态及网络")
                try:
                    await self.client.stop_background()
                except Exception:
                    # A connection whose close failed must never be duplicated.
                    self._set_state("failed", "飞书连接关闭失败，请重启渠道网关")
                    return
                if not self._infinite_retry or not self._started:
                    return
                self._set_state("reconnecting", "飞书连接失败，稍后自动重试")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def stop(self) -> None:
        self._started = False
        self._set_state("closed", "渠道已关闭")
        # Stop accepting events, then drain accepted callbacks before releasing
        # the SDK. Those callbacks only perform inbox admission, never the LLM run.
        try:
            async with asyncio.timeout(self.close_timeout):
                pending = [task for task in self._pending if task is not asyncio.current_task()]
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
        except TimeoutError:
            for task in list(self._pending):
                task.cancel()
            await asyncio.gather(*list(self._pending), return_exceptions=True)
        finally:
            self._callback = None
            if self._connect_task and not self._connect_task.done():
                self._connect_task.cancel()
                await asyncio.gather(self._connect_task, return_exceptions=True)
            self._connect_task = None
            try:
                await self.client.stop_background()
            except Exception:
                self._set_state("failed", "飞书连接关闭失败")
                raise RuntimeError("Feishu connection close failed") from None
            finally:
                self._replies.clear()
                self._envelopes.clear()

    async def test_connection(self, timeout: float) -> dict[str, Any]:
        # A second WS connection load-balances messages away from production.
        # REST checks credentials and bot capability without subscribing.
        try:
            async with asyncio.timeout(timeout):
                success = await self._probe_credentials()
        except Exception:
            success = False
        return {"account_id": self.account.id, "success": success,
                "state": "connected" if success else "failed",
                "authenticated": success, "updated_at": datetime.now(timezone.utc).isoformat(),
                "message": "飞书应用凭据验证成功（未创建测试长连接）" if success else
                           "飞书应用验证失败，请检查 App ID、App Secret、机器人能力及网络"}

    async def _probe_credentials(self) -> bool:
        # Mint a fresh token rather than using the SDK's process-global token
        # cache (keyed by app ID, not secret). This detects rotated/invalid
        # secrets even while an existing connection still has a valid token.
        request = (BaseRequest.builder().http_method(HttpMethod.POST)
                   .uri("/open-apis/auth/v3/tenant_access_token/internal")
                   .body({"app_id": self.account.bot_id, "app_secret": self.account.secret}).build())
        response = await self.client.client.arequest(request)
        if not response.success():
            return False
        token = json.loads(response.raw.content).get("tenant_access_token")
        if not token:
            return False
        request = (BaseRequest.builder().http_method(HttpMethod.GET)
                   .uri("/open-apis/bot/v3/info").token_types({AccessTokenType.TENANT}).build())
        option = RequestOption.builder().tenant_access_token(token).build()
        response = await self.client.client.arequest(request, option)
        if not response.success():
            return False
        bot = json.loads(response.raw.content).get("bot") or {}
        return bool(bot.get("open_id") or bot.get("openid"))

    def _capture_envelope(self, payload: dict) -> None:
        event = payload.get("event") or {}
        mid = (event.get("message") or {}).get("message_id")
        if not mid:
            return
        header, sender = payload.get("header") or {}, event.get("sender") or {}
        self._envelopes[mid] = {"event_id": header.get("event_id"),
                                "tenant_key": sender.get("tenant_key") or header.get("tenant_key")}
        while len(self._envelopes) > self.max_pending * 2:
            self._envelopes.popitem(last=False)

    async def _receive(self, message: Any) -> None:
        # Runs on the official SDK's background loop. Bridge explicitly to the
        # gateway loop; never use its async HTTP/DB resources from an SDK thread.
        if not self._started or self._loop is None:
            return
        if not self._slots.acquire(blocking=False):
            logger.error("Feishu admission queue reached its capacity")
            return
        try:
            envelope = self._envelopes.pop(message.message_id, {})
            future = asyncio.run_coroutine_threadsafe(self._admit(message, envelope), self._loop)
            await asyncio.wrap_future(future)
        finally:
            self._slots.release()

    async def _admit(self, message: Any, envelope: dict) -> None:
        task = asyncio.current_task()
        self._pending.add(task)
        try:
            quote = None
            parent_id = message.reply_to_message_id or message.raw.get("parent_id")
            if parent_id:
                try:
                    async with asyncio.timeout(5):
                        quote = await self.client.fetch_inbound_message(parent_id)
                    # A provider result is still scoped to the triggering chat.
                    if quote is not None and quote.chat_id != message.chat_id:
                        quote = None
                except Exception:
                    logger.debug("Feishu quoted message unavailable")
            mapped = parse_message(message, envelope=envelope, quote=quote)
            if self._callback:
                await self._callback(mapped)
        except UnsupportedMessage:
            logger.debug("Ignored unsupported or empty Feishu message")
        except Exception:
            logger.error("Feishu message admission failed")
        finally:
            self._pending.discard(task)

    async def download_media(self, media: InboundMedia) -> tuple[bytes, str | None]:
        handle = media.handle
        if not isinstance(handle, MediaHandle):
            raise ValueError("Invalid Feishu media handle")
        try:
            async with asyncio.timeout(30):
                data = await self.client.download_resource(handle.file_key, handle.resource_type,
                                                           message_id=handle.message_id)
            if not isinstance(data, bytes) or not data:
                raise ValueError("Empty resource")
            return data, media.filename
        except Exception:
            raise RuntimeError("Feishu media download failed") from None

    def new_reply_id(self) -> str:
        return str(uuid.uuid4())

    async def reply_loading(self, message: InboundMessage, reply_id: str, content: str = "") -> None:
        await self.reply(message, reply_id, f"{content}\n\n处理中…" if content else "处理中…", False)

    async def reply(self, message: InboundMessage, reply_id: str, content: str, finish: bool) -> None:
        handle = message.reply_handle
        if not isinstance(handle, ReplyHandle):
            raise ValueError("Invalid Feishu reply handle")
        state = self._replies.get(reply_id)
        if state is None:
            if len(self._replies) >= self.max_pending:
                raise RuntimeError("Feishu reply capacity reached")
            state = self._replies[reply_id] = ReplyState()
        try:
            async with state.lock:
                if state.card_id is None and not state.static:
                    try:
                        state.card_id = await self.client.create_card_instance({
                            "schema": "2.0", "config": {"streaming_mode": True},
                            "body": {"elements": [{"tag": "markdown", "element_id": "answer",
                                                     "content": content or "处理中…"}]},
                        })
                    except Exception:
                        # Without CardKit permission, ordinary final replies
                        # still work. No visible message has been sent yet.
                        state.static = True
                if state.static:
                    if finish:
                        result = await self.client.send(handle.chat_id, {"markdown": content}, {
                            "receive_id_type": "chat_id", "reply_to": handle.message_id,
                            "reply_in_thread": bool(handle.thread_id), "reply_target_gone": "fail",
                        })
                        if not result.success:
                            raise RuntimeError("Feishu final reply rejected")
                    return
                if not state.sent:
                    # A timeout can happen after Feishu accepted the message.
                    # Do not resend the card on the final delivery attempt and
                    # turn an uncertain preview into duplicate visible replies.
                    if state.send_attempted:
                        raise RuntimeError("Feishu card delivery was not confirmed")
                    state.send_attempted = True
                    result = await self.client.send_card_by_reference(
                        handle.chat_id, state.card_id, receive_id_type="chat_id",
                        reply_to=handle.message_id, reply_in_thread=bool(handle.thread_id),
                        reply_target_gone="fail",
                    )
                    if not result.success:
                        raise RuntimeError("Feishu card reply rejected")
                    state.sent = True
                state.sequence += 1
                await self.client.update_card_element_content(state.card_id, "answer", content or " ", state.sequence)
                if finish:
                    state.sequence += 1
                    await self.client.finish_streaming_card(state.card_id, state.sequence)
        except Exception:
            raise RuntimeError("Feishu reply failed") from None
        finally:
            if finish:
                self._replies.pop(reply_id, None)


__all__ = ["FeishuSDKAdapter"]
