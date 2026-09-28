"""Gateway adapter managing one isolated official Feishu SDK worker per app."""
from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from deep_agent.channels.contracts import InboundMedia, InboundMessage, MessageCallback
from .worker import worker_entry
from deep_agent.channels.outbound import DeliveryError
from .outbound import message_capabilities


class FeishuAdapter:
    max_pending = 128

    def __init__(self, account: Any, infinite_retry: bool = True, *, _worker_target=worker_entry) -> None:
        self.account = account
        self._infinite_retry = infinite_retry
        self._worker_target = _worker_target
        self._process = None
        self._connection = None
        self._reader = None
        self._loop = None
        self._callback = None
        self._pending: dict[str, asyncio.Future] = {}
        self._receivers: set[asyncio.Task] = set()
        self._start_lock = asyncio.Lock()
        self._send_lock = threading.Lock()
        self._started = False
        self._monitor = None
        self._closing = False
        self._status = self._state("closed", "渠道已关闭")

    @staticmethod
    def _state(state, message):
        return {"state": state, "message": message, "authenticated": state == "connected",
                "updated_at": datetime.now(timezone.utc).isoformat()}

    @staticmethod
    def connection_fingerprint(account: Any) -> str:
        data = [account.channel_type, account.transport, account.bot_id, account.secret,
                json.dumps((account.settings or {}).get("feishu") or {}, sort_keys=True)]
        return hashlib.sha256("\0".join(data).encode()).hexdigest()

    message_capabilities = staticmethod(message_capabilities)

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    async def _ensure_worker(self):
        async with self._start_lock:
            if self._closing:
                raise RuntimeError("Feishu adapter is stopping")
            if self._process is not None:
                if self._process.is_alive():
                    return
                await self._reap()
            self._loop = asyncio.get_running_loop()
            parent, child = multiprocessing.get_context("spawn").Pipe()
            self._connection = parent
            values = {"id": self.account.id, "bot_id": self.account.bot_id, "secret": self.account.secret,
                      "settings": self.account.settings or {}}
            self._process = multiprocessing.get_context("spawn").Process(
                target=self._worker_target, args=(child, values, self._infinite_retry),
                name="feishu-sdk-worker", daemon=True,
            )
            try:
                self._process.start()
            except Exception:
                parent.close(); child.close()
                self._process = None; self._connection = None
                raise RuntimeError("Feishu worker could not start") from None
            child.close()
            connection, process = self._connection, self._process
            def read_pipe():
                try:
                    while True:
                        packet = connection.recv()
                        self._loop.call_soon_threadsafe(self._dispatch, packet, process)
                except (EOFError, OSError, RuntimeError):
                    if not self._loop.is_closed():
                        self._loop.call_soon_threadsafe(self._lost, process)
            self._reader = threading.Thread(target=read_pipe, name="feishu-gateway-ipc", daemon=True)
            self._reader.start()

    def _dispatch(self, packet: dict, process):
        if process is not self._process:
            return
        kind = packet.get("kind")
        if kind == "result":
            future = self._pending.get(packet.get("id"))
            if future and not future.done():
                if packet.get("delivery_error"):
                    error = packet["delivery_error"]
                    future.set_exception(DeliveryError(error["code"], error["message"], error["unknown"]))
                elif packet.get("error"):
                    future.set_exception(RuntimeError("Feishu SDK operation failed"))
                else:
                    future.set_result(packet.get("result"))
        elif kind == "status" and not self._closing:
            self._status = packet["status"]
        elif kind == "message" and not self._closing and self._callback:
            if len(self._receivers) >= self.max_pending:
                self._status = self._state("failed", "飞书消息接收队列已满")
                return
            task = asyncio.create_task(self._receive(packet))
            self._receivers.add(task)
            task.add_done_callback(self._receivers.discard)

    def _lost(self, process):
        if process is not self._process:
            return
        if not self._closing:
            self._status = self._state("failed", "飞书连接进程已退出，正在恢复" if self._infinite_retry else "飞书连接进程已退出")
        for future in self._pending.values():
            if not future.done():
                future.set_exception(RuntimeError("Feishu worker disconnected"))

    def _send(self, packet):
        with self._send_lock:
            if self._connection is None:
                raise RuntimeError("Feishu worker unavailable")
            self._connection.send(packet)

    async def _rpc(self, method: str, *args, timeout: float = 35):
        if len(self._pending) >= self.max_pending:
            raise RuntimeError("Feishu worker capacity reached")
        identifier = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending[identifier] = future
        try:
            async with asyncio.timeout(timeout):
                await asyncio.to_thread(self._send, {"kind": "call", "id": identifier, "method": method, "args": args})
                return await future
        except TimeoutError:
            raise RuntimeError("Feishu worker operation timed out") from None
        except (EOFError, OSError):
            raise RuntimeError("Feishu worker unavailable") from None
        finally:
            self._pending.pop(identifier, None)
            if method in {"send_component", "resolve_users"} and not future.done():
                future.cancel()
            if method in {"send_component", "resolve_users"} and future.cancelled():
                try:
                    await asyncio.to_thread(self._send, {"kind": "cancel", "id": identifier})
                except (RuntimeError, OSError):
                    pass

    async def _receive(self, packet):
        try:
            await self._callback(packet["message"])
            await asyncio.to_thread(self._send, {"kind": "admitted", "id": packet["id"]})
        except asyncio.CancelledError:
            raise
        except Exception:
            self._status = self._state("failed", "飞书消息接纳失败")

    async def start(self, on_message: MessageCallback) -> None:
        if self._started:
            return
        self._callback = on_message
        self._closing = False
        await self._ensure_worker()
        self._status = self._state("connecting", "正在连接飞书")
        try:
            self._status = await self._rpc("start", timeout=15)
        except BaseException:
            await self.stop()
            raise
        self._started = True
        if self._infinite_retry:
            self._monitor = asyncio.create_task(self._supervise(), name="feishu-worker-supervisor")

    async def _supervise(self):
        delay = 1.0
        while self._started:
            await asyncio.sleep(delay)
            if self._closing or not self._started:
                return
            if self._process is not None and self._process.is_alive():
                delay = 1.0
                continue
            try:
                await self._ensure_worker()
                self._status = await self._rpc("start", timeout=15)
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception:
                self._status = self._state("failed", "飞书连接进程恢复失败，稍后重试")
                delay = min(delay * 2, 30.0)

    async def _reap(self):
        process = self._process
        if process is not None:
            await asyncio.to_thread(process.join, 2)
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 3)
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join, 2)
            if process.is_alive():
                raise RuntimeError("Feishu worker did not exit")
        if self._connection is not None:
            self._connection.close()
        if self._reader is not None:
            await asyncio.to_thread(self._reader.join, 2)
        self._process = self._connection = self._reader = None

    async def stop(self) -> None:
        self._closing = True
        self._started = False
        if self._monitor and self._monitor is not asyncio.current_task():
            self._monitor.cancel()
            await asyncio.gather(self._monitor, return_exceptions=True)
        self._monitor = None
        try:
            current = asyncio.current_task()
            pending = [task for task in self._receivers if task is not current]
            if pending:
                try:
                    async with asyncio.timeout(12):
                        await asyncio.gather(*pending, return_exceptions=True)
                except TimeoutError:
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
            if self._process is not None and self._process.is_alive():
                try:
                    await self._rpc("stop", timeout=15)
                except Exception:
                    pass  # The owning parent still forcibly reaps the process.
        finally:
            self._callback = None
            await self._reap()
            self._status = self._state("closed", "渠道已关闭")

    async def test_connection(self, timeout: float) -> dict[str, Any]:
        # A probe is always isolated, including when the managed worker died.
        # It can neither revive a stopped transport nor consume bot messages.
        probe = FeishuAdapter(self.account, infinite_retry=False, _worker_target=self._worker_target)
        try:
            await probe._ensure_worker()
            return await probe._rpc("test_connection", timeout, timeout=timeout + 5)
        except Exception:
            return {"account_id": self.account.id, "success": False,
                    **self._state("failed", "飞书应用验证失败，请检查凭据及网络")}
        finally:
            await probe.stop()

    def new_reply_id(self) -> str:
        return str(uuid.uuid4())

    async def send_component(self, target, component, *, reply_to_current=False):
        if target.account_id != self.account.id:
            raise DeliveryError("invalid_target", "渠道账号与当前会话不一致")
        if self._closing or not self._started:
            raise DeliveryError("not_connected", "飞书渠道未连接")
        try:
            return await self._rpc("send_component", target, component, reply_to_current, timeout=120)
        except DeliveryError:
            raise
        except Exception:
            raise DeliveryError("send_unknown", "飞书发送结果未知，请确认是否已收到，勿重复发送", unknown=True) from None

    async def resolve_users(self, user_ids: list[str], *, timeout: float = 3.0) -> dict:
        if self._closing or not self._started:
            raise RuntimeError("Feishu worker unavailable")
        return await self._rpc("resolve_users", user_ids, timeout, timeout=timeout + 1)

    async def download_media(self, media: InboundMedia) -> tuple[bytes, str | None]:
        return await self._rpc("download_media", media)

    async def reply_loading(self, message: InboundMessage, reply_id: str, content: str = "") -> None:
        await self._rpc("reply_loading", message, reply_id, content)

    async def reply(self, message: InboundMessage, reply_id: str, content: str, finish: bool) -> None:
        await self._rpc("reply", message, reply_id, content, finish)


__all__ = ["FeishuAdapter"]
