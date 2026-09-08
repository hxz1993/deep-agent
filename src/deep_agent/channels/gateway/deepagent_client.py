from __future__ import annotations

import json
import mimetypes
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import aiohttp

from deep_agent.channels.gateway.config import GatewayConfig
from deep_agent.channels.contracts import InboundMedia


@dataclass(frozen=True)
class StreamEvent:
    event: str
    data: dict[str, Any]


class DeepAgentChannelClient:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None, connect=15, sock_connect=15)
            )

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _headers(self, account) -> dict[str, str]:
        headers = {
            "user": account.session_user_id,
            "X-Deep-Agent-Channel-Token": self.config.internal_token,
            "X-Deep-Agent-Channel-Account": account.id,
        }
        if self.config.platform_authorization:
            headers["authorization"] = self.config.platform_authorization
        if self.config.platform_cookie:
            headers["cookie"] = self.config.platform_cookie
        return headers

    async def upload_media(self, account, media: InboundMedia, data: bytes, filename: str) -> dict[str, Any]:
        await self.start()
        assert self._session is not None
        form = aiohttp.FormData()
        resolved_name = filename or media.filename
        guessed_mime, _ = mimetypes.guess_type(resolved_name)
        content_type = (
            guessed_mime
            if media.mime == "application/octet-stream" and guessed_mime
            else media.mime
        )
        form.add_field(
            "file",
            data,
            filename=resolved_name,
            content_type=content_type,
        )
        async with self._session.post(
            f"{self.config.api_url}/api/v1/uploads",
            data=form,
            headers=self._headers(account),
        ) as response:
            payload = await response.json(content_type=None)
            if response.status >= 400:
                raise RuntimeError(f"DeepAgent upload failed with HTTP {response.status}")
            if not isinstance(payload, dict) or not payload.get("id"):
                raise RuntimeError("DeepAgent upload returned an invalid response")
            return payload

    async def _session_action(self, account, session_id: str, action: str, *, method: str = "GET", agent_id: str | None = None) -> dict[str, Any]:
        from urllib.parse import quote

        await self.start()
        assert self._session is not None
        async with self._session.request(
            method, f"{self.config.api_url}/api/v1/sessions/{quote(session_id, safe='')}/{action}",
            headers=self._headers(account), params={"agent_id": agent_id} if agent_id else None,
            timeout=aiohttp.ClientTimeout(total=3),
        ) as response:
            if response.status >= 400:
                raise RuntimeError(f"DeepAgent session operation failed with HTTP {response.status}")
            payload = await response.json(content_type=None)
            if not isinstance(payload, dict):
                raise RuntimeError("DeepAgent session operation returned invalid response")
            return payload

    async def cancel_session(self, account, session_id: str, *, agent_id: str | None = None) -> dict[str, Any]:
        return await self._session_action(account, session_id, "cancel", method="POST", agent_id=agent_id)

    async def run_status(self, account, session_id: str, *, agent_id: str | None = None) -> dict[str, Any]:
        return await self._session_action(account, session_id, "run-status", agent_id=agent_id)

    async def stream_chat(
        self,
        account,
        *,
        session_id: str,
        message: str,
        attachments: list[dict[str, Any]],
        context_refs: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        await self.start()
        assert self._session is not None
        body: dict[str, Any] = {
            "agent_id": account.agent_id,
            "session_id": session_id,
            "message": message,
        }
        if attachments:
            body["attachments"] = attachments
        if context_refs:
            body["context_refs"] = context_refs
        async with self._session.post(
            f"{self.config.api_url}/api/v1/chat/stream",
            json=body,
            headers={**self._headers(account), "Accept": "text/event-stream"},
        ) as response:
            if response.status >= 400:
                await response.read()
                raise RuntimeError(f"DeepAgent chat failed with HTTP {response.status}")
            event_name = "message"
            data_lines: list[str] = []
            async for raw_line in response.content:
                line = raw_line.decode("utf-8").rstrip("\r\n")
                if not line:
                    if data_lines:
                        raw_data = "\n".join(data_lines)
                        try:
                            payload = json.loads(raw_data)
                        except json.JSONDecodeError as exc:
                            raise RuntimeError("DeepAgent returned malformed SSE JSON") from exc
                        if isinstance(payload, dict):
                            yield StreamEvent(event_name, payload)
                    event_name = "message"
                    data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
            if data_lines:
                payload = json.loads("\n".join(data_lines))
                if isinstance(payload, dict):
                    yield StreamEvent(event_name, payload)


__all__ = ["DeepAgentChannelClient", "StreamEvent"]
