"""Bounded, administrator-owned SDK registration flows; credentials stay server-side."""
from __future__ import annotations

import asyncio
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit


async def register_with_sdk(**kwargs):
    from lark_oapi import aregister_app
    return await aregister_app(**kwargs)


class RegistrationError(ValueError):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


@dataclass
class Registration:
    owner: str
    deadline: float
    app_name: str = "DeepAgent"
    status: str = "starting"
    verification_url: str | None = None
    app_id: str | None = None
    secret: str | None = field(default=None, repr=False)
    error: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class FeishuRegistrationService:
    def __init__(self, *, register=register_with_sdk, ttl: float = 600, capacity: int = 16):
        self.register = register
        self.ttl = ttl
        self.capacity = capacity
        self.entries: dict[str, Registration] = {}

    def _get(self, registration_id: str, owner: str) -> Registration:
        entry = self.entries.get(registration_id)
        if entry is None or entry.owner != owner:
            raise RegistrationError(404, "扫码任务不存在或已失效")
        if time.monotonic() >= entry.deadline and not entry.lock.locked():
            entry.secret = None
            entry.verification_url = None
            entry.status = "expired"
            if entry.task and not entry.task.done():
                entry.task.cancel()
        return entry

    def snapshot(self, registration_id: str, owner: str) -> dict[str, Any]:
        entry = self._get(registration_id, owner)
        return dict(registration_id=registration_id, status=entry.status,
                    verification_url=entry.verification_url, app_id=entry.app_id,
                    expires_in=max(0, int(entry.deadline - time.monotonic())), error=entry.error)

    async def start(self, owner: str, *, app_name: str = "") -> dict[str, Any]:
        # Terminal jobs do not accumulate indefinitely or retain old credentials.
        for key, entry in list(self.entries.items()):
            if not entry.lock.locked() and (
                time.monotonic() >= entry.deadline
                or entry.status in {"failed", "denied", "cancelled", "consumed", "expired"}
            ):
                if entry.task and not entry.task.done():
                    entry.task.cancel()
                entry.secret = None
                del self.entries[key]
        if len(self.entries) >= self.capacity:
            raise RegistrationError(429, "扫码任务已满，请稍后重试")
        if sum(e.owner == owner and e.status in {"starting", "waiting", "authorized"}
               for e in self.entries.values()) >= 2:
            raise RegistrationError(429, "请先完成或取消已有扫码任务")
        key = secrets.token_urlsafe(32)
        entry = Registration(owner=owner, deadline=time.monotonic() + self.ttl,
                             app_name=app_name.strip() or "DeepAgent")
        self.entries[key] = entry
        entry.task = asyncio.create_task(self._run(entry), name="feishu-registration")
        return self.snapshot(key, owner)

    async def _run(self, entry: Registration) -> None:
        timeout = None

        def qr(info):
            url = info.get("url", "")
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.hostname not in {
                "accounts.feishu.cn", "open.feishu.cn"} or parsed.username or parsed.password:
                raise ValueError("Unexpected registration URL")
            entry.verification_url = url
            entry.deadline = min(entry.deadline, time.monotonic() + max(1, int(info.get("expire_in", 600))))
            if timeout is not None:
                timeout.reschedule(asyncio.get_running_loop().time() + max(0, entry.deadline - time.monotonic()))
            entry.status = "waiting"

        def status(info):
            if info.get("status") == "domain_switched":
                raise ValueError("Only Feishu is supported")

        try:
            async with asyncio.timeout(max(0, entry.deadline - time.monotonic())) as timeout:
                result = await self.register(on_qr_code=qr, on_status_change=status,
                                             source="deep-agent", create_only=True,
                                             addons={"preset": False, "scopes": {"tenant": [
                                                 "im:message.p2p_msg:readonly", "im:message.group_at_msg:readonly",
                                                 "im:message:send_as_bot", "im:message:readonly",
                                                 "im:resource", "cardkit:card:write",
                                             ]}, "events": {"items": {"tenant": ["im.message.receive_v1"]}}},
                                             app_preset={"name": entry.app_name, "desc": "DeepAgent 飞书助手"})
            app_id, secret = result.get("client_id"), result.get("client_secret")
            if not isinstance(app_id, str) or not app_id.strip() or not isinstance(secret, str) or not secret.strip():
                raise ValueError("Missing credentials")
            entry.app_id, entry.secret = app_id.strip(), secret.strip()
            entry.status = "authorized"
            entry.verification_url = None
            await asyncio.sleep(max(0, entry.deadline - time.monotonic()))
            async with entry.lock:
                entry.secret = None
                entry.status = "expired"
        except asyncio.CancelledError:
            entry.secret = None
            raise
        except Exception as exc:
            # SDK exception messages may contain credentials or remote response bodies.
            kind = type(exc).__name__
            entry.status = "expired" if isinstance(exc, TimeoutError) or kind == "AppExpiredError" else "denied" if kind == "AppAccessDeniedError" else "failed"
            entry.error = {"expired": "扫码已过期，请重新创建", "denied": "授权已取消", "failed": "飞书扫码创建失败，请重试"}[entry.status]
            entry.secret = None
            entry.verification_url = None

    async def cancel(self, registration_id: str, owner: str) -> None:
        entry = self._get(registration_id, owner)
        async with entry.lock:
            if entry.task:
                entry.task.cancel()
                await asyncio.gather(entry.task, return_exceptions=True)
            entry.secret = None
            entry.verification_url = None
            entry.status = "cancelled"

    @asynccontextmanager
    async def credentials(self, registration_id: str, owner: str):
        entry = self._get(registration_id, owner)
        async with entry.lock:
            if entry.status != "authorized" or not entry.secret or time.monotonic() >= entry.deadline:
                raise RegistrationError(409, "请先完成有效的扫码授权")
            yield entry.app_id, entry.secret
            # Only consume after the database create succeeds; failed saves can be retried.
            entry.secret = None
            entry.status = "consumed"
            if entry.task:
                entry.task.cancel()
                await asyncio.gather(entry.task, return_exceptions=True)

    async def aclose(self):
        tasks = [entry.task for entry in self.entries.values() if entry.task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for entry in self.entries.values():
            entry.secret = None
        self.entries.clear()


registration_service = FeishuRegistrationService()
