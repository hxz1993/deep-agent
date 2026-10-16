"""Independent Moonshot Search API; no auxiliary model or Chat loop."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx2

from openai import AsyncOpenAI
from pydantic_ai import ToolFailed
from deep_agent.web.provider_api import parse_fetch, post_json, tool_endpoint


def search_client(api_key: str | None, base_url: str | None = None) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url or "https://api.moonshot.cn/v1",
        timeout=60,
        max_retries=0,
        http_client=httpx2.AsyncClient(timeout=60, follow_redirects=False),
    )


async def execute_search(client: AsyncOpenAI, query: str) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        raise ToolFailed("Web Search query cannot be empty")
    result: dict[str, Any] = await client.post(
        "/tools/search",
        cast_to=dict[str, Any],
        body={"text_query": query.strip(), "limit": 5, "timeout_seconds": 30, "include_content": False},
    )
    rows = result.get("search_results")
    if not isinstance(rows, list) or not rows:
        raise ToolFailed("Kimi Web Search returned no results")
    if not all(isinstance(row, dict) and isinstance(row.get("url"), str) and row["url"].strip() for row in rows):
        raise ToolFailed("Kimi Web Search returned invalid results")
    for row in rows:
        parsed = urlsplit(row["url"])
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ToolFailed("Kimi Web Search returned invalid source URLs")
    return {"search_results": rows[:5], "untrusted_external_content": True}


async def execute_fetch(*, api_key: str, base_url: str | None, url: str) -> dict[str, str]:
    endpoint = tool_endpoint(base_url, default="https://api.moonshot.cn/v1", tool="fetch")
    payload = await post_json(endpoint, api_key, {"url": url})
    # The standalone Kimi endpoint returns `markdown`, unlike DeepSeek's
    # `content`. Do not accept an answer/summary as a substitute for this body.
    if isinstance(payload, dict):
        payload = {"title": payload.get("title"), "content": payload.get("markdown")}
    return parse_fetch(payload, "Kimi")
