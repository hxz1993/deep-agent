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
    PolicyConfig, SafetyConfig, SecurityConfig, TextBatchConfig, TransportConfig, NameCacheConfig,
)
from loguru import logger

from deep_agent.channels.contracts import InboundMedia, InboundMessage, MessageCallback
from .messages import MediaHandle, ReplyHandle, UnsupportedMessage, parse_message
from .sdk_logging import configure_sdk_logging
from .profiles import FeishuProfiles


@dataclass
class ReplyState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    card_id: str | None = None
    sequence: int = 0
    static: bool = False
    sent: bool = False
    send_attempted: bool = False
    message_id: str = ""
    reaction_attempted: bool = False
    reaction_id: str | None = None
    loading_visible: bool = False


class FeishuSDKAdapter:
    """One app connection; the shared gateway still owns all business execution."""
    max_pending = 128
    close_timeout = 12.0
    reaction_timeout = 2.0

    def __init__(self, account: Any, infinite_retry: bool = True) -> None:
        self.account = account
        self._infinite_retry = infinite_retry
        settings = (account.settings or {}).get("feishu") or {}
        self._profiles_enabled = settings.get("user_details_enabled") is True
        self._profiles = FeishuProfiles(self._fetch_names)
        policy = PolicyConfig(
            require_mention=True,
            group_policy="allowlist" if settings.get("group_allowlist") else "open",
            group_allowlist=settings.get("group_allowlist") or None,
        )
        configure_sdk_logging()
        self.client = FeishuChannel(name_lookup=self._lookup_sender_names, config=ChannelConfig(
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
                                  fetch_interactive_card=False, name_cache=NameCacheConfig(enabled=False)),
            security=SecurityConfig(mode="strict", max_concurrent_ws_handlers=64,
                                    max_ws_fragment_bytes=8 * 1024 * 1024,
                                    max_ws_fragment_parts=128),
            media_cache=MediaCacheConfig(enabled=False),
            # Roster lookup requires extra permissions and bypasses our opt-in.
            resolve_sender_names=False,
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
        self._reaction_warning: str | None = None
        self.client.on("raw", self._capture_envelope)
        self.client.on("message", self._receive)
        self.client.on("reconnecting", lambda *_: self._set_state("reconnecting", "飞书连接中断，正在重连"))
        self.client.on("reconnected", lambda *_: self._set_state("connected", "已连接飞书"))
        self.client.on("error", lambda *_: logger.warning("Feishu SDK operation failed"))

    async def _fetch_names(self, user_ids: list[str]) -> dict[str, str]:
        from lark_channel.api.contact.v3.model.batch_user_request import BatchUserRequest
        request = BatchUserRequest.builder().user_id_type("open_id").user_ids(user_ids).build()
        response = await self.client.client.contact.v3.user.abatch(request)
        if not response.success():
            return {}
        requested = set(user_ids)
        return {item.open_id: getattr(item, "name", None) or getattr(item, "en_name", None)
                for item in (getattr(getattr(response, "data", None), "items", None) or [])
                if getattr(item, "open_id", None) in requested
                and (getattr(item, "name", None) or getattr(item, "en_name", None))}

    async def _lookup_sender_names(self, user_ids: list[str]) -> dict[str, str]:
        if not self._profiles_enabled or not self._started or self._loop is None:
            return {}
        # The official SDK normalizes events on its background loop. All
        # profile state and HTTP lookup live on the worker's owning loop.
        async def lookup():
            async with asyncio.timeout(3):
                return await self._profiles.resolve(user_ids)
        try:
            if asyncio.get_running_loop() is self._loop:
                return await lookup()
            future = asyncio.run_coroutine_threadsafe(lookup(), self._loop)
            return await asyncio.wrap_future(future)
        except Exception:
            return {}

    async def resolve_users(self, user_ids: list[str], timeout: float = 3.0) -> dict:
        status = "disabled" if not self._profiles_enabled else "unavailable"
        names = {}
        if self._profiles_enabled and self._started:
            try:
                async with asyncio.timeout(timeout):
                    names = await self._profiles.resolve(user_ids)
            except TimeoutError:
                names = self._profiles.cached(user_ids)
        items = [{"sender_id": uid, "userid": None, "name": names.get(uid),
                  "status": "resolved" if uid in names else status}
                 for uid in dict.fromkeys(user_ids)]
        return {"items": items, "complete": all(item["status"] == "resolved" for item in items)}

    @staticmethod
    def message_capabilities():
        from .outbound import message_capabilities
        return message_capabilities()

    async def send_component(self, target, component, *, reply_to_current=False):
        from .outbound import send_component
        from deep_agent.channels.outbound import DeliveryError
        if target.account_id != self.account.id:
            raise DeliveryError("invalid_target", "渠道账号与当前会话不一致")
        return await send_component(self.client, target, component, reply_to_current=reply_to_current)

    def _set_state(self, state: str, message: str) -> None:
        if not self._started and state in {"connected", "reconnecting"}:
            return
        self._state, self._message = state, message
        self._updated_at = datetime.now(timezone.utc)

    def status(self) -> dict[str, Any]:
        return {"state": self._state, "message": self._message + (
                    f"；{self._reaction_warning}" if self._reaction_warning and self._state == "connected" else ""),
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
                await asyncio.gather(*(self._close_reply(state) for state in list(self._replies.values())))
                await self.client.stop_background()
            except Exception:
                self._set_state("failed", "飞书连接关闭失败")
                raise RuntimeError("Feishu connection close failed") from None
            finally:
                self._replies.clear()
                self._envelopes.clear()
                self._profiles.clear()

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

    def _reaction_result(self, operation: str, result=None, *, reason: str = "响应无效") -> None:
        if result is not None and result.success:
            self._reaction_warning = None
        else:
            # SDK hints/raw responses may contain tokens, URLs or message text.
            # Only an integer platform error code leaves the isolated worker.
            code = getattr(getattr(result, "error", None), "raw_code", None)
            detail = f"飞书错误码 {code}" if type(code) is int else reason
            if type(code) is int and code in {99991672, 99991679}:
                detail += "，请开通 im:message.reactions:write_only 权限并发布应用"
            self._reaction_warning = f"Get 表情{operation}失败（{detail}）"
        self._updated_at = datetime.now(timezone.utc)

    async def _add_processing_reaction(self, state: ReplyState) -> None:
        if state.reaction_attempted:
            return
        state.reaction_attempted = True
        try:
            async with asyncio.timeout(self.reaction_timeout):
                # The convenience typing helper discards all platform errors.
                result = await self.client.add_reaction(state.message_id, "Get")
            raw = result.raw if isinstance(result.raw, dict) else {}
            data = raw.get("data") or {}
            reaction_id = data.get("reaction_id") if isinstance(data, dict) else None
            if result.success and isinstance(reaction_id, str) and reaction_id:
                state.reaction_id = reaction_id
                self._reaction_result("添加", result)
            else:
                self._reaction_result("添加", result if not result.success else None)
        except TimeoutError:
            self._reaction_result("添加", reason="请求超时")
        except Exception:
            # Optional feedback must not block delivering the actual reply.
            self._reaction_result("添加", reason="请求不可用")

    async def _remove_processing_reaction(self, state: ReplyState) -> None:
        reaction_id, state.reaction_id = state.reaction_id, None
        if not reaction_id:
            return
        try:
            async with asyncio.timeout(self.reaction_timeout):
                result = await self.client.remove_reaction(state.message_id, reaction_id)
            self._reaction_result("移除", result)
        except TimeoutError:
            self._reaction_result("移除", reason="请求超时")
        except Exception:
            self._reaction_result("移除", reason="请求不可用")

    async def reply_loading(self, message: InboundMessage, reply_id: str, content: str = "") -> None:
        # Preserve cumulative text; the separate loading icon remains beneath it.
        await self.reply(message, reply_id, content, False)

    async def _remove_loading(self, state: ReplyState) -> None:
        if not state.loading_visible:
            return
        from lark_channel.api.cardkit.v1.model.delete_card_element_request import DeleteCardElementRequest
        from lark_channel.api.cardkit.v1.model.delete_card_element_request_body import DeleteCardElementRequestBody
        state.sequence += 1
        request = (DeleteCardElementRequest.builder().card_id(state.card_id)
                   .element_id("loading_icon")
                   .request_body(DeleteCardElementRequestBody.builder().sequence(state.sequence).build())
                   .build())
        result = await self.client.client.cardkit.v1.card_element.adelete(request)
        if not result.success():
            raise RuntimeError("Feishu loading indicator removal failed")
        state.loading_visible = False

    async def _finish_card(self, state: ReplyState) -> None:
        if not state.card_id or not state.send_attempted or state.static:
            return
        try:
            state.sequence += 1
            await self.client.finish_streaming_card(state.card_id, state.sequence)
        finally:
            await self._remove_loading(state)

    async def _close_reply(self, state: ReplyState) -> None:
        try:
            async with asyncio.timeout(self.close_timeout), state.lock:
                await self._finish_card(state)
        except Exception:
            logger.warning("Feishu card cleanup could not be confirmed")
        finally:
            await self._remove_processing_reaction(state)

    async def reply(self, message: InboundMessage, reply_id: str, content: str, finish: bool) -> None:
        handle = message.reply_handle
        if not isinstance(handle, ReplyHandle):
            raise ValueError("Invalid Feishu reply handle")
        state = self._replies.get(reply_id)
        if state is None:
            if len(self._replies) >= self.max_pending:
                raise RuntimeError("Feishu reply capacity reached")
            state = self._replies[reply_id] = ReplyState(message_id=handle.message_id)
        try:
            async with state.lock:
                if not finish:
                    await self._add_processing_reaction(state)
                if state.card_id is None and not state.static:
                    try:
                        elements = [{"tag": "markdown", "element_id": "answer", "content": content}]
                        if not finish:
                            # Official openclaw-lark builder.ts loading footer (MIT).
                            # streaming_mode only controls text updates, not this animation.
                            elements.append({
                                "tag": "markdown", "element_id": "loading_icon", "content": " ",
                                "icon": {"tag": "custom_icon", "size": "16px 16px",
                                         "img_key": "img_v3_02vb_496bec09-4b43-4773-ad6b-0cdd103cd2bg"},
                            })
                        state.card_id = await self.client.create_card_instance({
                            "schema": "2.0", "config": {
                                "streaming_mode": True,
                                "streaming_config": {
                                    "print_frequency_ms": {"default": 50},
                                    "print_step": {"default": 2},
                                    "print_strategy": "fast",
                                },
                            },
                            "body": {"elements": elements},
                        })
                        state.loading_visible = not finish
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
                if content:
                    state.sequence += 1
                    await self.client.update_card_element_content(state.card_id, "answer", content, state.sequence)
        except Exception:
            raise RuntimeError("Feishu reply failed") from None
        finally:
            if finish:
                try:
                    # Also close cards whose send or last text update had an
                    # uncertain result; never resend a second visible message.
                    async with state.lock:
                        await self._finish_card(state)
                except Exception:
                    raise RuntimeError("Feishu reply failed") from None
                finally:
                    try:
                        await self._remove_processing_reaction(state)
                    finally:
                        self._replies.pop(reply_id, None)


__all__ = ["FeishuSDKAdapter"]
