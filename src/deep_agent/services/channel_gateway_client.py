from __future__ import annotations

import os
import json
import uuid
from dataclasses import asdict
from typing import Any

import aiohttp


class ChannelGatewayUnavailableError(ConnectionError):
    pass


class ChannelGatewayClient:
    def __init__(self) -> None:
        self.base_url = os.environ.get("DEEP_AGENT_CHANNEL_GATEWAY_URL", "").strip().rstrip("/")
        self.internal_token = os.environ.get("DEEP_AGENT_CHANNEL_INTERNAL_TOKEN", "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.internal_token}"}

    async def statuses(self) -> dict[str, Any]:
        return await self._request("GET", "/accounts/status")

    async def test_account(self, account_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/accounts/{account_id}/test")

    async def send_message(self, source, agent_run_id, tool_call_id, components, *, reply=False):
        operation_id = uuid.uuid5(uuid.NAMESPACE_URL, f"deep-agent-channel:{agent_run_id}:{tool_call_id}").hex
        metadata = {key: getattr(source, key) for key in
                    ("account_id", "channel_run_id", "session_id", "agent_id", "user_id")}
        metadata.update(agent_run_id=agent_run_id, tool_call_id=tool_call_id, reply=reply,
                        components=[{k: v for k, v in asdict(c).items() if k != "data"} for c in components])
        form = aiohttp.FormData()
        form.add_field("metadata", json.dumps(metadata), content_type="application/json")
        for index, component in enumerate(components):
            if component.data is not None:
                form.add_field(f"media_{index}", component.data, filename=component.filename,
                               content_type=component.mime)
        try:
            # The gateway gives each ordered component up to 120 seconds.
            # Keep the HTTP request alive for the whole batch, including its
            # terminal receipt; a fixed 180s budget truncates valid multi-file sends.
            timeout = aiohttp.ClientTimeout(total=120 * len(components) + 30, connect=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(f"{self.base_url}/messages/send", headers=self._headers(), data=form) as response:
                    payload = await response.json(content_type=None)
                    if response.status >= 500 or not isinstance(payload, dict):
                        return {"operation_id": operation_id, "status": "unknown",
                                "error": "渠道发送结果无法确认，请勿盲目重复发送"}
                    if response.status >= 400:
                        return {"operation_id": operation_id, "status": "failed",
                                "error": payload.get("detail", "渠道发送被拒绝")}
                    return payload
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return {"operation_id": operation_id, "status": "unknown",
                    "error": "渠道发送结果无法确认，请勿盲目重复发送"}

    async def _request(self, method: str, path: str) -> dict[str, Any]:
        if not self.configured:
            raise ChannelGatewayUnavailableError("Channel Gateway is not deployed")
        timeout = aiohttp.ClientTimeout(total=12)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=self._headers(),
                ) as response:
                    payload = await response.json(content_type=None)
                    if response.status >= 400:
                        detail = payload.get("detail") if isinstance(payload, dict) else None
                        raise ChannelGatewayUnavailableError(
                            str(detail or f"Gateway HTTP {response.status}")
                        )
                    if not isinstance(payload, dict):
                        raise ChannelGatewayUnavailableError("Gateway returned an invalid response")
                    return payload
        except ChannelGatewayUnavailableError:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise ChannelGatewayUnavailableError(type(exc).__name__) from exc


__all__ = ["ChannelGatewayClient", "ChannelGatewayUnavailableError"]
