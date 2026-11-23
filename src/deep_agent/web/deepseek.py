"""DeepSeek standalone Search/Fetch APIs; no auxiliary model."""
from __future__ import annotations

from typing import Any
from pydantic_ai import ModelRetry, ToolFailed
from deep_agent.web.provider_api import parse_fetch, post_json, tool_endpoint, valid_source_url

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_SEARCH_COUNT = 5
MAX_SEARCH_QUERY_LENGTH = 128


def search_endpoint(base_url: str | None) -> str:
    return tool_endpoint(base_url, default=DEFAULT_BASE_URL, tool="search")


def parse_search_results(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ToolFailed("DeepSeek 搜索响应格式无效")
    rows = payload["results"]
    if any(not isinstance(row, dict) or not valid_source_url(row.get("url")) for row in rows):
        raise ToolFailed("DeepSeek 搜索来源格式无效")
    return payload


async def execute_search(
    *,
    api_key: str,
    base_url: str | None,
    query: str,
    count: int = DEFAULT_SEARCH_COUNT,
    need_content: bool = False,
    ori_question: str | None = None,
    localization: str | None = None,
) -> dict[str, Any]:
    query = query.strip()
    if not query or len(query) > MAX_SEARCH_QUERY_LENGTH:
        raise ModelRetry("DeepSeek 搜索词须为1～128个Unicode字符，请精简查询")
    body = {"query": query, "count": count, "need_content": need_content}
    if ori_question is not None:
        body["ori_question"] = ori_question
    if localization is not None:
        body["localization"] = localization
    payload = await post_json(search_endpoint(base_url), api_key, body)
    return parse_search_results(payload)


async def execute_fetch(*, api_key: str, base_url: str | None, url: str) -> dict[str, Any]:
    payload = await post_json(tool_endpoint(base_url, default=DEFAULT_BASE_URL, tool="fetch"),
                              api_key, {"url": url})
    return parse_fetch(payload, "DeepSeek", content_field="content")
