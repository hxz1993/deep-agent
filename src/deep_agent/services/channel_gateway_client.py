from __future__ import annotations

import os
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
