from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GatewayConfig:
    api_url: str
    internal_token: str
    poll_interval: float
    stream_interval: float
    test_timeout: float
    host: str
    port: int
    platform_authorization: str
    platform_cookie: str

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        token = os.environ.get("DEEP_AGENT_CHANNEL_INTERNAL_TOKEN", "").strip()
        if not token:
            raise RuntimeError("DEEP_AGENT_CHANNEL_INTERNAL_TOKEN is required")
        return cls(
            api_url=os.environ.get(
                "DEEP_AGENT_CHANNEL_API_URL", "http://127.0.0.1:8000"
            ).strip().rstrip("/"),
            internal_token=token,
            poll_interval=max(
                1.0,
                float(os.environ.get("DEEP_AGENT_CHANNEL_POLL_INTERVAL", "3")),
            ),
            stream_interval=max(
                0.1,
                float(os.environ.get("DEEP_AGENT_CHANNEL_STREAM_INTERVAL", "0.35")),
            ),
            test_timeout=max(
                3.0,
                float(os.environ.get("DEEP_AGENT_CHANNEL_TEST_TIMEOUT", "10")),
            ),
            host=os.environ.get("DEEP_AGENT_CHANNEL_HOST", "127.0.0.1").strip(),
            port=int(os.environ.get("DEEP_AGENT_CHANNEL_PORT", "8010")),
            platform_authorization=os.environ.get(
                "DEEP_AGENT_CHANNEL_PLATFORM_AUTHORIZATION", ""
            ).strip(),
            platform_cookie=os.environ.get(
                "DEEP_AGENT_CHANNEL_PLATFORM_COOKIE", ""
            ).strip(),
        )


__all__ = ["GatewayConfig"]
