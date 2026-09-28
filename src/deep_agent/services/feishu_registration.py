"""Feishu SDK registration protocol."""
from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit

from deep_agent.services.channel_registration import Registration, RegistrationService


async def register_with_sdk(**kwargs):
    from lark_oapi import aregister_app
    return await aregister_app(**kwargs)


class FeishuRegistrationService(RegistrationService):
    def __init__(self, *, register=register_with_sdk, ttl: float = 600, capacity: int = 16):
        super().__init__(ttl=ttl, capacity=capacity)
        self.register = register

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
                # Keep the PersonalAgent template; only add sender-name field access.
                result = await self.register(
                    on_qr_code=qr, on_status_change=status,
                    app_preset={"name": entry.app_name},
                    addons={"scopes": {"tenant": ["contact:user.base:readonly"]}},
                )
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


registration_service = FeishuRegistrationService()
