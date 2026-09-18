"""Moonshot Formula search: shared by current-model and fixed-provider modes."""
from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI
from pydantic_ai import ToolFailed

FORMULA_PATH = "/formulas/moonshot/web-search:latest"


def search_client(api_key: str | None, base_url: str | None = None) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url or "https://api.moonshot.cn/v1",
        timeout=60,
        max_retries=0,
    )


async def execute_search(client: AsyncOpenAI, arguments: str) -> str:
    fiber: dict[str, Any] = await client.post(
        f"{FORMULA_PATH}/fibers",
        cast_to=dict[str, Any],
        body={"name": "web_search", "arguments": arguments},
    )
    if fiber.get("status") != "succeeded":
        raise ToolFailed("Kimi Formula Web Search execution failed")
    context = fiber.get("context") or {}
    output = context.get("output") or context.get("encrypted_output")
    if not isinstance(output, str) or not output.strip():
        raise ToolFailed("Kimi Formula Web Search returned no result")
    # Preserve the encrypted result verbatim. Only Moonshot's model can read it.
    return output
