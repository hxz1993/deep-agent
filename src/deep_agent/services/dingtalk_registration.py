"""DingTalk device registration; credentials never leave the server lifecycle."""
from __future__ import annotations

import asyncio
import time
import re
import math
import uuid

from deep_agent.diagnostics import emit
from urllib.parse import urlsplit

import httpx

from .channel_registration import Registration, RegistrationService


class DingtalkRegistrationService(RegistrationService):
    def __init__(self, *, ttl=600, capacity=16, transport=None, poll_interval=3):
        super().__init__(ttl=ttl, capacity=capacity)
        self.transport = transport
        self.poll_interval = poll_interval

    @staticmethod
    async def _post(client, operation, payload):
        response = await client.post(
            f"https://oapi.dingtalk.com/app/registration/{operation}", json=payload,
            timeout=httpx.Timeout(180 if operation == "poll" else 30, connect=15),
        )
        if operation != "poll" or response.is_error:
            emit("channel_registration_response", phase=operation, http_status=response.status_code)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and (operation != "poll" or data.get("errcode", 0) not in (0, "0")):
            # Only protocol enums/codes, never URLs, credentials or raw errors.
            code = str(data.get("errcode", 0))
            state = str(data.get("status") or "")
            emit("channel_registration_response", phase=operation,
                 error_code=code if re.fullmatch(r"-?[0-9]{1,12}", code) else "redacted",
                 status=state if state in {"WAITING", "SELECTING", "SCANNED", "CREATING", "SUCCESS", "FAIL", "EXPIRED"} else "absent_or_invalid")
        if not isinstance(data, dict) or data.get("errcode", 0) not in (0, "0"):
            raise ValueError("DingTalk registration request failed")
        return data

    async def _run(self, entry: Registration):
        trace = uuid.uuid4().hex[:12]
        stage = "init"
        previous_state = None
        unknown_since = None
        emit("channel_registration_state", request_id=trace, phase=stage, status="starting")
        try:
            async with asyncio.timeout(max(0, entry.deadline - time.monotonic())) as lifetime_timeout:
                async with httpx.AsyncClient(timeout=15, follow_redirects=False, transport=self.transport) as client:
                    data = await self._post(client, "init", {"source": "DING_DWS_CLAW"})
                    nonce = data.get("nonce")
                    if not isinstance(nonce, str) or not nonce:
                        raise ValueError("Missing nonce")
                    stage = "begin"
                    data = await self._post(client, "begin", {"nonce": nonce})
                    code, url = data.get("device_code"), data.get("verification_uri_complete")
                    if not isinstance(code, str) or not code or not isinstance(url, str):
                        raise ValueError("Missing device registration")
                    parsed = urlsplit(url)
                    host = parsed.hostname or ""
                    if (parsed.scheme != "https" or not (host == "dingtalk.com" or host.endswith(".dingtalk.com"))
                            or parsed.username or parsed.password or parsed.port not in (None, 443)
                            or any(ord(char) < 32 for char in url)):
                        raise ValueError("Invalid DingTalk verification URL")
                    lifetime = data.get("expires_in")
                    if isinstance(lifetime, (int, float)) and not isinstance(lifetime, bool) and math.isfinite(lifetime) and lifetime > 0:
                        entry.deadline = min(entry.deadline, time.monotonic() + lifetime)
                        lifetime_timeout.reschedule(asyncio.get_running_loop().time() + max(0, entry.deadline - time.monotonic()))
                    interval = data.get("interval", self.poll_interval)
                    if not isinstance(interval, (int, float)) or isinstance(interval, bool) or not math.isfinite(interval):
                        interval = self.poll_interval
                    interval = max(self.poll_interval, min(interval, 30))
                    entry.verification_url, entry.status = url, "waiting"
                    stage = "poll"
                    while time.monotonic() < entry.deadline:
                        try:
                            data = await self._post(client, "poll", {"device_code": code})
                        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                            if isinstance(exc, httpx.HTTPStatusError) and not (exc.response.status_code in {408, 429} or exc.response.status_code >= 500):
                                raise
                            # Poll is read-only: keep the same task/device code alive.
                            entry.error = "钉钉状态查询暂时中断，正在重试，请勿重复创建应用。"
                            await asyncio.sleep(interval)
                            continue
                        if time.monotonic() >= entry.deadline:
                            raise TimeoutError
                        entry.error = None
                        state = str(data.get("status") or "").strip().upper()
                        if state != previous_state:
                            emit("channel_registration_state", request_id=trace, phase=stage,
                                 status=state if state in {"WAITING", "SELECTING", "SCANNED", "CREATING", "SUCCESS", "FAIL", "EXPIRED"} else "unknown")
                            previous_state = state
                        if state == "SUCCESS":
                            app_id, secret = data.get("client_id"), data.get("client_secret")
                            if (not isinstance(app_id, str) or not app_id.strip() or len(app_id.strip()) > 191
                                    or not isinstance(secret, str) or not secret.strip()):
                                raise ValueError("Missing credentials")
                            entry.app_id, entry.secret = app_id.strip(), secret.strip()
                            break
                        if state == "EXPIRED":
                            raise TimeoutError
                        if state == "FAIL":
                            raise ValueError("Registration rejected")
                        if state not in {"WAITING", "SELECTING", "SCANNED", "CREATING"}:
                            # The official connector allows bounded recovery for
                            # new/intermediate platform statuses, never authorizes them.
                            unknown_since = unknown_since or time.monotonic()
                            if time.monotonic() - unknown_since >= 120:
                                raise ValueError("Registration rejected")
                            entry.error = "等待钉钉确认授权状态，请勿重复创建机器人。"
                            await asyncio.sleep(interval)
                            continue
                        unknown_since = None
                        entry.status = {"WAITING": "waiting", "SELECTING": "selecting", "SCANNED": "scanned", "CREATING": "creating"}[state]
                        await asyncio.sleep(interval)
                    else:
                        raise TimeoutError
            entry.deadline = time.monotonic() + 600
            entry.status, entry.verification_url = "authorized", None
            await asyncio.sleep(max(0, entry.deadline - time.monotonic()))
            async with entry.lock:
                entry.secret, entry.status = None, "expired"
        except asyncio.CancelledError:
            entry.secret = None
            raise
        except Exception as exc:
            emit("channel_registration_failed", level="WARNING", request_id=trace,
                 phase=stage, error_type=type(exc).__name__,
                 reason=str(exc) if isinstance(exc, ValueError) and str(exc) in {
                     "Missing nonce", "Missing device registration", "Invalid DingTalk verification URL",
                     "Missing credentials", "Registration rejected", "DingTalk registration request failed"} else "request_failed")
            entry.status = "expired" if isinstance(exc, TimeoutError) else "failed"
            entry.error = "扫码已过期，请重新生成" if entry.status == "expired" else "钉钉扫码创建失败，请重试或手动关联"
            entry.secret = entry.verification_url = None


registration_service = DingtalkRegistrationService()
