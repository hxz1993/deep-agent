from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Any

from aibot import WSClient, WSClientOptions, WsFrame, generate_req_id
from loguru import logger

from deep_agent.channels.contracts import InboundMedia, InboundMessage, MessageCallback

from .messages import MediaHandle, ReplyHandle, parse_message, reply_handle
from .sdk_logger import SafeSdkLogger


class WecomAdapter:
    """Thin adapter over the official SDK; it owns no WebSocket protocol logic."""

    close_timeout = 2.0

    def __init__(self, account: Any, infinite_retry: bool = True) -> None:
        self.account = account
        self.client = WSClient(WSClientOptions(
            bot_id=account.bot_id, secret=account.secret,
            max_reconnect_attempts=-1 if infinite_retry else 0,
            logger=SafeSdkLogger(),
        ))
        self._authenticated = asyncio.Event()
        self._state = "closed"
        self._message = "渠道已关闭"
        self._updated_at = datetime.now(timezone.utc)
        self._on_message: MessageCallback | None = None
        self._connect_task: asyncio.Task | None = None
        self._callback_tasks: set[asyncio.Task] = set()
        self._started = False
        self._bind_handlers()

    @staticmethod
    def connection_fingerprint(account: Any) -> str:
        # Agent, session user and display name do not change the SDK connection.
        values = [account.channel_type, account.transport, account.bot_id, account.secret]
        return hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()

    async def start(self, on_message: MessageCallback) -> None:
        self._on_message = on_message
        if self._started:
            return
        self._started = True
        self._set_state("connecting", "正在连接企业微信")
        self._connect_task = asyncio.create_task(self._connect(), name="wecom-sdk-connect")

    async def _connect(self) -> None:
        try:
            await self.client.connect()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._set_state(*self._safe_connection_error(exc))

    async def stop(self) -> None:
        self._started = False
        self._on_message = None
        self._set_state("closed", "渠道已关闭")
        try:
            current = asyncio.current_task()
            callbacks = [task for task in self._callback_tasks if task is not current and not task.done()]
            for task in callbacks:
                task.cancel()
            if callbacks:
                await asyncio.gather(*callbacks, return_exceptions=True)
            if self._connect_task is not None and not self._connect_task.done():
                self._connect_task.cancel()
                await asyncio.gather(self._connect_task, return_exceptions=True)
        finally:
            self._connect_task = None
            await self._disconnect()

    async def _disconnect(self) -> None:
        try:
            # The SDK schedules socket shutdown itself. Wait only on its public
            # state; never access the manager/socket or duplicate close frames.
            self.client.disconnect()
            async with asyncio.timeout(self.close_timeout):
                await asyncio.sleep(0)
                while self.client.is_connected:
                    await asyncio.sleep(0.02)
        except TimeoutError:
            self._set_state("failed", "企微连接关闭超时")
            raise RuntimeError("WeCom connection close timed out") from None
        except Exception:
            self._set_state("failed", "企微连接关闭失败")
            raise RuntimeError("WeCom connection close failed") from None

    def status(self) -> dict[str, Any]:
        return {
            "state": self._state, "message": self._message,
            "authenticated": self._authenticated.is_set(),
            "updated_at": self._updated_at.isoformat(),
        }

    async def test_connection(self, timeout: float) -> dict[str, Any]:
        if not self._authenticated.is_set() and self._started:
            try:
                # Existing connections keep using the SDK's native reconnect loop.
                await asyncio.wait_for(self._authenticated.wait(), timeout=timeout)
            except TimeoutError:
                pass
        status = self.status()
        success = status["authenticated"]
        if not success and status["state"] in {"connecting", "reconnecting"}:
            status.update(state="failed", message="企微认证超时，连接仍按 SDK 策略处理")
        return {"account_id": self.account.id, "success": success, **status}

    async def download_media(self, media: InboundMedia) -> tuple[bytes, str | None]:
        if not isinstance(media.handle, MediaHandle):
            raise ValueError("Invalid WeCom media handle")
        try:
            return await self.client.download_file(media.handle.url, media.handle.aes_key)
        except Exception:
            raise RuntimeError("WeCom media download failed") from None

    def new_reply_id(self) -> str:
        return generate_req_id("deepagent")

    async def reply(
        self, message: InboundMessage, reply_id: str, content: str, finish: bool
    ) -> None:
        if not isinstance(message.reply_handle, ReplyHandle):
            raise ValueError("Invalid WeCom reply handle")
        try:
            await self.client.reply_stream(message.reply_handle.frame, reply_id, content, finish)
        except Exception:
            raise RuntimeError("WeCom reply failed") from None

    def _set_state(self, state: str, message: str) -> None:
        self._state, self._message = state, message
        self._updated_at = datetime.now(timezone.utc)
        if state == "connected":
            self._authenticated.set()
        else:
            self._authenticated.clear()

    def _bind_handlers(self) -> None:
        def authenticated(*_args) -> None:
            if self._started:
                self._set_state("connected", "已连接企业微信")

        def connected(*_args) -> None:
            if self._started:
                self._set_state("connecting", "连接已建立，正在认证")

        def reconnecting(*_args) -> None:
            if self._started:
                self._set_state("reconnecting", "连接中断，正在自动重连")

        def disconnected(*_args) -> None:
            if self._started:
                self._set_state("reconnecting", "连接已断开，等待自动重连")

        def error(exc: object) -> None:
            if self._started:
                self._set_state(*self._safe_connection_error(exc))

        self.client.on("authenticated", authenticated)
        self.client.on("connected", connected)
        self.client.on("reconnecting", reconnecting)
        self.client.on("disconnected", disconnected)
        self.client.on("error", error)
        # SDK emits both generic and specific events for the same callback.
        # Generic message also receives video in SDK 1.0.2.
        self.client.on("message", self._receive_message)
        self.client.on("event", self._receive_event)

    async def _receive_message(self, frame: WsFrame) -> None:
        if not self._started or self._on_message is None:
            return
        callback = self._on_message
        task = asyncio.current_task()
        if task is not None:
            self._callback_tasks.add(task)
        try:
            try:
                message = parse_message(frame)
            except (ValueError, TypeError):
                logger.warning("Ignored unsupported WeCom message")
                await self._reply_unsupported(frame)
                return
            try:
                await callback(message)
            except Exception:
                logger.error("Channel callback failed for WeCom message")
        finally:
            if task is not None:
                self._callback_tasks.discard(task)

    async def _receive_event(self, _frame: WsFrame) -> None:
        # No welcome message or cards are configured in this release.
        if self._started:
            logger.debug("Ignored WeCom event callback")

    async def _reply_unsupported(self, frame: WsFrame) -> None:
        try:
            await self.client.reply_stream(
                reply_handle(frame).frame, self.new_reply_id(),
                "暂不支持这类企微消息，请发送文本、图片、文件、语音或视频。", True,
            )
        except Exception:
            logger.warning("Unable to reply to unsupported WeCom message")

    @staticmethod
    def _safe_connection_error(error: object) -> tuple[str, str]:
        text = str(error).lower()
        if any(marker in text for marker in ("conflict", "duplicate", "kick", "already")):
            return "conflict", "同一 Bot 存在其他长连接，当前连接发生冲突"
        if any(marker in text for marker in ("auth", "secret", "401", "403")):
            return "failed", "企微认证失败，请检查 Bot ID 和 Secret"
        return "failed", "企微连接失败，SDK 将按策略重试"


__all__ = ["WecomAdapter"]
