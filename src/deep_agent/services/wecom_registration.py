"""WeCom official QR protocol, independent of message transport and rendering."""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from urllib.parse import urlsplit

import httpx

from deep_agent.services.channel_registration import Registration, RegistrationService


class _QrRequestLogFilter(logging.Filter):
    def filter(self, record):
        # httpx INFO includes the full request URL, including the polling credential.
        return "https://work.weixin.qq.com/ai/qc/" not in record.getMessage()


logging.getLogger("httpx").addFilter(_QrRequestLogFilter())


class WecomRegistrationService(RegistrationService):
    def __init__(self, *, ttl: float = 300, capacity: int = 16,
                 transport: httpx.AsyncBaseTransport | None = None, poll_interval: float = 3):
        super().__init__(ttl=ttl, capacity=capacity)
        self.transport = transport
        self.poll_interval = poll_interval

    @staticmethod
    async def _data(client: httpx.AsyncClient, endpoint: str, params: dict) -> dict:
        # Endpoints are constants; never fetch the authorization URL returned remotely.
        response = await client.get(f"https://work.weixin.qq.com/ai/qc/{endpoint}", params=params)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ValueError("Invalid response")
        return data

    async def _run(self, entry: Registration) -> None:
        try:
            async with asyncio.timeout(max(0, entry.deadline - time.monotonic())):
                async with httpx.AsyncClient(timeout=10, follow_redirects=False,
                                             transport=self.transport) as client:
                    plat = {"darwin": 1, "win32": 2, "linux": 3}.get(sys.platform, 0)
                    data = await self._data(client, "generate", {"source": "deep-agent", "plat": plat})
                    scode, url = data.get("scode"), data.get("auth_url")
                    if not isinstance(scode, str) or not scode.strip() or not isinstance(url, str):
                        raise ValueError("Missing QR session")
                    parsed = urlsplit(url)
                    if (parsed.scheme != "https" or parsed.hostname != "work.weixin.qq.com"
                            or parsed.username or parsed.password or parsed.port not in (None, 443)
                            or parsed.path != "/ai/qc/c" or any(ord(c) < 32 for c in url)):
                        raise ValueError("Unexpected authorization URL")
                    entry.verification_url = url
                    entry.status = "waiting"
                    while True:
                        data = await self._data(client, "query_result", {"scode": scode})
                        if data.get("status") == "success":
                            info = data.get("bot_info")
                            bot_id, secret = (info.get("botid"), info.get("secret")) if isinstance(info, dict) else (None, None)
                            if (not isinstance(bot_id, str) or not bot_id.strip() or len(bot_id.strip()) > 191
                                    or not isinstance(secret, str) or not secret.strip()):
                                raise ValueError("Missing bot credentials")
                            entry.app_id, entry.secret = bot_id.strip(), secret.strip()
                            break
                        # Official protocol only defines success; all other states wait until TTL.
                        await asyncio.sleep(self.poll_interval)
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
            entry.status = "expired" if isinstance(exc, TimeoutError) else "failed"
            entry.error = "扫码已过期，请重新生成" if entry.status == "expired" else "企微扫码创建失败，请重试"
            entry.secret = None
            entry.verification_url = None


registration_service = WecomRegistrationService()
