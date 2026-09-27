"""Bounded administrator-owned registration lifecycle; secrets stay server-side."""
from __future__ import annotations

import asyncio
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any


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


class RegistrationService:
    def __init__(self, *, ttl: float = 600, capacity: int = 16):
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
        entry.task = asyncio.create_task(self._run(entry), name=f"{type(self).__name__}-registration")
        return self.snapshot(key, owner)

    async def _run(self, entry: Registration) -> None:
        raise NotImplementedError

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

