from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from aibot import WSClient, WSClientOptions, generate_req_id
from loguru import logger

from deep_agent.channel_gateway.config import GatewayConfig
from deep_agent.channel_gateway.deepagent_client import DeepAgentChannelClient
from deep_agent.channel_gateway.messages import InboundMessage, parse_message
from deep_agent.channel_gateway.sdk_logger import SafeSdkLogger
from deep_agent.db.stores.channel import ChannelStore


@dataclass
class AccountRuntime:
    account: Any
    client: WSClient
    fingerprint: str
    authenticated: asyncio.Event = field(default_factory=asyncio.Event)
    state: str = "connecting"
    message: str = "正在连接企业微信"
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    connect_task: asyncio.Task | None = None

    def set_state(self, state: str, message: str) -> None:
        self.state = state
        self.message = message
        self.updated_at = datetime.now(timezone.utc)
        if state == "connected":
            self.authenticated.set()
        elif state in {"failed", "conflict", "closed"}:
            self.authenticated.clear()


class ChannelRuntime:
    def __init__(self, store: ChannelStore, config: GatewayConfig) -> None:
        self.store = store
        self.config = config
        self.deep_agent = DeepAgentChannelClient(config)
        self._accounts: dict[str, AccountRuntime] = {}
        self._disabled: dict[str, datetime] = {}
        self._peer_locks: dict[tuple[str, str, str], asyncio.Lock] = {}
        self._poll_task: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        interrupted = await self.store.interrupt_incomplete()
        if interrupted:
            logger.warning("Channel Gateway recovered {} interrupted runs", interrupted)
        await self.deep_agent.start()
        await self.reconcile()
        self._poll_task = asyncio.create_task(self._poll(), name="channel-account-poller")

    async def stop(self) -> None:
        self._stopping = True
        if self._poll_task is not None:
            self._poll_task.cancel()
            await asyncio.gather(self._poll_task, return_exceptions=True)
        for account_id in list(self._accounts):
            await self._stop_account(account_id)
        await self.deep_agent.close()

    async def _poll(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(self.config.poll_interval)
                await self.reconcile()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - poller must survive database hiccups
                logger.error("Channel account reconcile failed error_type={}", type(exc).__name__)

    async def reconcile(self) -> None:
        rows = await self.store.list_accounts()
        live_ids = {row.id for row in rows}
        for stale_id in set(self._accounts) - live_ids:
            await self._stop_account(stale_id)
        self._disabled = {
            row.id: datetime.now(timezone.utc) for row in rows if not row.enabled
        }
        for row in rows:
            if not row.enabled:
                await self._stop_account(row.id)
                continue
            fingerprint = self._fingerprint(row)
            current = self._accounts.get(row.id)
            if current is not None and current.fingerprint == fingerprint:
                current.account = row
                continue
            if current is not None:
                await self._stop_account(row.id)
            await self._start_account(row, fingerprint)

    async def _start_account(self, account, fingerprint: str) -> AccountRuntime:
        client = self._new_client(account, infinite_retry=True)
        managed = AccountRuntime(account=account, client=client, fingerprint=fingerprint)
        self._bind_handlers(managed)
        self._accounts[account.id] = managed
        managed.connect_task = asyncio.create_task(
            self._connect(managed), name=f"wecom-connect-{account.id}"
        )
        return managed

    async def _connect(self, managed: AccountRuntime) -> None:
        try:
            await managed.client.connect()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - SDK error is reduced to a safe state
            state, message = self._safe_connection_error(exc)
            managed.set_state(state, message)

    async def _stop_account(self, account_id: str) -> None:
        managed = self._accounts.pop(account_id, None)
        if managed is None:
            return
        managed.set_state("closed", "渠道已关闭")
        if managed.connect_task is not None and not managed.connect_task.done():
            managed.connect_task.cancel()
        managed.client.disconnect()
        await asyncio.sleep(0)

    def _new_client(self, account, *, infinite_retry: bool) -> WSClient:
        return WSClient(
            WSClientOptions(
                bot_id=account.bot_id,
                secret=account.secret,
                max_reconnect_attempts=-1 if infinite_retry else 0,
                logger=SafeSdkLogger(),
            )
        )

    def _bind_handlers(self, managed: AccountRuntime) -> None:
        async def authenticated(*_args) -> None:
            managed.set_state("connected", "已连接企业微信")

        async def connected(*_args) -> None:
            managed.set_state("connecting", "连接已建立，正在认证")

        async def reconnecting(*_args) -> None:
            managed.set_state("reconnecting", "连接中断，正在自动重连")

        async def disconnected(*_args) -> None:
            if not self._stopping:
                managed.set_state("reconnecting", "连接已断开，等待自动重连")

        async def error(error: object) -> None:
            state, message = self._safe_connection_error(error)
            managed.set_state(state, message)

        async def message(frame: dict[str, Any]) -> None:
            await self._on_message(managed, frame)

        async def event(frame: dict[str, Any]) -> None:
            await self._on_event(managed, frame)

        managed.client.on("authenticated", authenticated)
        managed.client.on("connected", connected)
        managed.client.on("reconnecting", reconnecting)
        managed.client.on("disconnected", disconnected)
        managed.client.on("error", error)
        managed.client.on("message", message)
        managed.client.on("event", event)

    async def _on_message(self, managed: AccountRuntime, frame: dict[str, Any]) -> None:
        try:
            message = parse_message(frame)
        except ValueError as exc:
            logger.warning("Ignored unsupported WeCom message reason={}", type(exc).__name__)
            await self._reply_unsupported(managed.client, frame)
            return
        key = (managed.account.id, message.peer_type, message.peer_id)
        lock = self._peer_locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                await self._process_message(managed, message)
        finally:
            if not lock.locked() and not getattr(lock, "_waiters", None):
                self._peer_locks.pop(key, None)

    async def _process_message(
        self, managed: AccountRuntime, message: InboundMessage
    ) -> None:
        account = await self.store.get_account(managed.account.id)
        if account is None or not account.enabled:
            logger.info("Ignored WeCom message for disabled channel account")
            return
        managed.account = account
        previous = await self.store.get_message(account.id, message.message_id)
        if previous is not None and not self.store.can_retry(previous):
            # Dedupe before resolving a binding: a redelivery after an Agent
            # change must not create an empty replacement session.
            logger.info("Skipped duplicate WeCom message account_id={}", account.id)
            return
        resolved = await self.store.resolve_binding(
            account_id=account.id,
            peer_type=message.peer_type,
            peer_id=message.peer_id,
            peer_display_name=message.peer_display_name,
        )
        stream_id = generate_req_id("deepagent")
        claimed = await self.store.claim_message(
            account_id=account.id,
            binding_id=resolved.binding.id,
            external_message_id=message.message_id,
            external_request_id=message.request_id,
            sender_id=message.sender_id,
            session_id=resolved.session.session_id,
            payload_hash=message.payload_hash,
            stream_id=stream_id,
        )
        if not claimed.claimed:
            logger.info("Skipped duplicate WeCom message account_id={}", account.id)
            return

        run = claimed.run
        dispatched = False
        try:
            await managed.client.reply_stream(
                message.frame, stream_id, "正在处理…", False
            )
            attachments: list[dict[str, Any]] = []
            for media in message.media:
                data, filename = await managed.client.download_file(media.url, media.aes_key)
                attachments.append(
                    await self.deep_agent.upload_media(
                        account,
                        media,
                        data,
                        filename or media.filename,
                    )
                )

            # Persist the dispatch boundary BEFORE HTTP. Missing SSE run_id is
            # not proof that the Agent did not start (network/process failure).
            dispatched = True
            await self.store.update_run(
                run.id, processing_status="running", delivery_status="streaming"
            )
            output = ""
            final_status = "completed"
            final_seen = False
            last_sent = 0.0
            async for event in self.deep_agent.stream_chat(
                account,
                session_id=resolved.session.session_id,
                message=message.prompt,
                attachments=attachments,
            ):
                if event.event == "session":
                    agent_run_id = str(event.data.get("run_id") or "")
                    if agent_run_id:
                        await self.store.update_run(run.id, agent_run_id=agent_run_id)
                elif event.event == "text":
                    output += str(event.data.get("delta") or "")
                    now = time.monotonic()
                    if output and now - last_sent >= self.config.stream_interval:
                        await managed.client.reply_stream(
                            message.frame, stream_id, output, False
                        )
                        last_sent = now
                elif event.event == "final":
                    final_seen = True
                    final_status = str(event.data.get("status") or "completed")
                    if not output and isinstance(event.data.get("output"), str):
                        output = str(event.data["output"])
            if not final_seen:
                raise RuntimeError("DeepAgent stream ended without a final event")
            if final_status == "pending":
                suffix = "此操作需要在 DeepAgent 控制台继续完成。"
                output = f"{output}\n\n{suffix}".strip()
            if not output:
                output = "已处理完成，但没有可展示的文本结果。"
            await managed.client.reply_stream(message.frame, stream_id, output, True)
            await self.store.update_run(
                run.id,
                processing_status="completed",
                delivery_status="sent",
            )
        except asyncio.CancelledError:
            await self.store.update_run(
                run.id,
                processing_status="interrupted",
                delivery_status="failed",
                error_code="gateway_cancelled" if dispatched else "preflight_failed",
                error_message="Gateway processing was interrupted",
            )
            raise
        except Exception as exc:  # noqa: BLE001 - user receives a stable safe message
            logger.error(
                "WeCom message processing failed account_id={} error_type={}",
                account.id,
                type(exc).__name__,
            )
            await self.store.update_run(
                run.id,
                processing_status="failed",
                delivery_status="failed",
                error_code=type(exc).__name__ if dispatched else "preflight_failed",
                error_message="Channel message processing failed",
            )
            try:
                await managed.client.reply_stream(
                    message.frame,
                    stream_id,
                    "消息处理失败，请稍后重试或在 DeepAgent 控制台查看状态。",
                    True,
                )
            except Exception:
                logger.warning("Unable to deliver sanitized WeCom error response")

    async def _on_event(self, managed: AccountRuntime, frame: dict[str, Any]) -> None:
        body = frame.get("body") if isinstance(frame.get("body"), dict) else {}
        event = body.get("event") if isinstance(body.get("event"), dict) else {}
        event_type = str(event.get("eventtype") or "")
        logger.info("Received ignored WeCom event type={}", event_type or "unknown")

    async def _reply_unsupported(self, client: WSClient, frame: dict[str, Any]) -> None:
        try:
            stream_id = generate_req_id("deepagent")
            await client.reply_stream(
                frame,
                stream_id,
                "暂不支持这类企微消息，请发送文本、图片、文件、语音或视频。",
                True,
            )
        except Exception:
            logger.warning("Unable to reply to unsupported WeCom message")

    def status_payload(self) -> dict[str, Any]:
        accounts = [
            {
                "account_id": account_id,
                "state": runtime.state,
                "message": runtime.message,
                "authenticated": runtime.authenticated.is_set(),
                "updated_at": runtime.updated_at.isoformat(),
            }
            for account_id, runtime in sorted(self._accounts.items())
        ]
        accounts.extend(
            {
                "account_id": account_id,
                "state": "closed",
                "message": "渠道已关闭",
                "authenticated": False,
                "updated_at": updated.isoformat(),
            }
            for account_id, updated in sorted(self._disabled.items())
        )
        return {
            "state": "connected",
            "message": "Channel Gateway 运行正常",
            "accounts": accounts,
        }

    async def test_account(self, account_id: str) -> dict[str, Any]:
        account = await self.store.get_account(account_id)
        if account is None:
            raise LookupError("Channel account not found")
        current = self._accounts.get(account_id)
        if current is not None:
            if current.authenticated.is_set():
                return self._test_payload(account_id, True, "connected", "已连接企业微信")
            try:
                await asyncio.wait_for(
                    current.authenticated.wait(), timeout=self.config.test_timeout
                )
                return self._test_payload(account_id, True, "connected", "企微认证成功")
            except TimeoutError:
                return self._test_payload(account_id, False, current.state, current.message)

        client = self._new_client(account, infinite_retry=False)
        authenticated = asyncio.Event()
        failure: dict[str, str] = {}

        async def on_authenticated(*_args) -> None:
            authenticated.set()

        async def on_error(error: object) -> None:
            state, message = self._safe_connection_error(error)
            failure.update(state=state, message=message)

        client.on("authenticated", on_authenticated)
        client.on("error", on_error)
        try:
            await client.connect()
            await asyncio.wait_for(authenticated.wait(), timeout=self.config.test_timeout)
            return self._test_payload(
                account_id,
                True,
                "connected",
                "临时企微认证成功，连接已断开",
                warning="测试同一 Bot 时，企微可能中断该 Bot 的其他长连接。",
            )
        except TimeoutError:
            return self._test_payload(
                account_id,
                False,
                failure.get("state", "failed"),
                failure.get("message", "企微认证超时"),
                warning="临时连接已断开。",
            )
        except Exception as exc:  # noqa: BLE001
            state, message = self._safe_connection_error(exc)
            return self._test_payload(account_id, False, state, message)
        finally:
            client.disconnect()
            await asyncio.sleep(0)

    @staticmethod
    def _test_payload(
        account_id: str,
        success: bool,
        state: str,
        message: str,
        *,
        warning: str | None = None,
    ) -> dict[str, Any]:
        return {
            "account_id": account_id,
            "success": success,
            "state": state,
            "message": message,
            "warning": warning,
        }

    @staticmethod
    def _safe_connection_error(error: object) -> tuple[str, str]:
        text = str(error).lower()
        if any(marker in text for marker in ("conflict", "duplicate", "kick", "already")):
            return "conflict", "同一 Bot 存在其他长连接，当前连接发生冲突"
        if "auth" in text or "secret" in text or "401" in text or "403" in text:
            return "failed", "企微认证失败，请检查 Bot ID 和 Secret"
        return "failed", "企微连接失败，Gateway 将按策略重试"

    @staticmethod
    def _fingerprint(account) -> str:
        value = "\0".join(
            [
                account.bot_id,
                account.secret,
                account.agent_id,
                account.session_user_id,
            ]
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = ["AccountRuntime", "ChannelRuntime"]
