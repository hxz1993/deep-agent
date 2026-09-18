"""Independent DeepSeek search over Anthropic Messages, without an auxiliary Agent."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx2
from pydantic_ai import ToolFailed


def search_endpoint(base_url: str | None) -> str:
    """Keep custom endpoints on their configured origin; official chat uses a separate path."""
    parsed = urlsplit(base_url or "https://api.deepseek.com")
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ToolFailed("DeepSeek 搜索地址无效，请配置支持 Anthropic Messages 的供应商地址")
    path = parsed.path.rstrip("/")
    if parsed.hostname == "api.deepseek.com" and path in {"", "/v1"}:
        path = "/anthropic/v1"
    if path.endswith(("/chat/completions", "/responses")):
        raise ToolFailed("DeepSeek 搜索需要 Anthropic Messages 基址，不能使用聊天或 Responses 接口地址")
    if not path.endswith("/messages"):
        path += "/messages"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def parse_search_results(payload: Any) -> list[dict[str, str]]:
    """Accept structured search results only, never answer text or fabricated tool tags."""
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
        raise ToolFailed("DeepSeek 搜索响应格式无效")
    blocks = payload["content"]
    search_blocks = [b for b in blocks if isinstance(b, dict) and b.get("type") == "web_search_tool_result"]
    if not search_blocks:
        raise ToolFailed("本次响应未返回搜索执行成功的证据，无法确认搜索成功；普通文本回答不能作为依据。")
    snippets: dict[str, str] = {}
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        citations = block.get("citations")
        for cite in citations if isinstance(citations, list) else []:
            if isinstance(cite, dict) and isinstance(cite.get("url"), str) and isinstance(cite.get("cited_text"), str):
                snippets.setdefault(cite["url"], cite["cited_text"])
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for block in search_blocks:
        content = block.get("content")
        if isinstance(content, dict) and content.get("type") == "web_search_tool_result_error":
            code = content.get("error_code")
            # Only known provider error codes are safe to carry verbatim.
            safe_code = code if isinstance(code, str) and code in {"invalid_input", "unavailable", "max_uses_exceeded", "too_many_requests", "query_too_long"} else "provider_error"
            raise ToolFailed(f"DeepSeek 搜索执行失败：{safe_code}")
        if not isinstance(content, list):
            raise ToolFailed("DeepSeek 搜索结果格式无效")
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "web_search_result":
                continue
            url = item.get("url")
            if not isinstance(url, str):
                continue
            try:
                parsed = urlsplit(url)
                valid = parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password
            except ValueError:
                valid = False
            if not valid or url in seen:
                continue
            seen.add(url)
            result = {"url": url}
            for source, target in (("title", "title"), ("page_age", "published_at")):
                if isinstance(item.get(source), str):
                    result[target] = item[source]
            if url in snippets:
                result["snippet"] = snippets[url]
            results.append(result)
    if not results:
        raise ToolFailed("DeepSeek 搜索未返回有效来源")
    return results


async def execute_search(*, api_key: str, base_url: str | None, model: str, query: str) -> list[dict[str, str]]:
    async with httpx2.AsyncClient(timeout=60, follow_redirects=False) as client:
        response = await client.post(
            search_endpoint(base_url),
            headers={"x-api-key": api_key, "authorization": f"Bearer {api_key}",
                     "anthropic-version": "2023-06-01"},
            json={"model": model, "max_tokens": 4096,
                  "messages": [{"role": "user", "content": [{"type": "text", "text": f"Perform a web search for the query: {query}"}]}],
                  "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]},
        )
        response.raise_for_status()
        return parse_search_results(response.json())
