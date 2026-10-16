"""Shared transport for standalone Search/Fetch APIs, without chat calls."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx2
from pydantic_ai import ToolFailed


def tool_endpoint(base_url: str | None, *, default: str, tool: str) -> str:
    try:
        parsed = urlsplit(base_url or default)
        _ = parsed.port
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            raise ValueError("invalid base")
    except ValueError as exc:
        raise ToolFailed("网络工具供应商地址无效") from exc
    path = parsed.path.rstrip("/")
    if parsed.hostname == "api.deepseek.com" and path in {"", "/v1", "/anthropic", "/anthropic/v1"}:
        path = "/v1"
    elif not path:
        path = "/v1"
    if path.endswith(("/chat/completions", "/responses", "/messages")):
        raise ToolFailed("网络工具需要供应商基址，不能使用完整聊天接口地址")
    if path.endswith(("/tools/search", "/tools/fetch")):
        path = path.rsplit("/", 1)[0]
    if not path.endswith("/tools"):
        path += "/tools"
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/{tool}", "", ""))


async def post_json(endpoint: str, api_key: str, body: dict[str, Any]) -> Any:
    # Redirects must not forward a provider credential to another origin.
    async with httpx2.AsyncClient(timeout=30, follow_redirects=False) as client:
        response = await client.post(
            endpoint, headers={"Authorization": f"Bearer {api_key}"}, json=body,
        )
        response.raise_for_status()
        return response.json()


def valid_source_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        return bool(parsed.scheme in {"http", "https"} and parsed.hostname
                    and parsed.username is None and parsed.password is None)
    except ValueError:
        return False


def parse_fetch(payload: Any, provider: str) -> dict[str, str]:
    if (not isinstance(payload, dict) or not isinstance(payload.get("title"), str)
            or not isinstance(payload.get("content"), str)):
        raise ToolFailed(f"{provider} Fetch 返回格式无效")
    if not payload["content"].strip():
        raise ToolFailed(f"{provider} Fetch 未返回可用网页正文")
    return {"title": payload["title"], "content": payload["content"]}
