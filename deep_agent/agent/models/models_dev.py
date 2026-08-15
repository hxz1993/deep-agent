"""Strict models.dev client used only by the administrator refresh endpoint."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_MODELS_DEV_CATALOG_URL = "https://models.dev/catalog.json"
DEFAULT_MODELS_DEV_LABS_URL = "https://models.dev/labs"

_LAB_SEARCH_INDEX = re.compile(
    r"""<script[^>]*id=["']search-index["'][^>]*>([\s\S]*?)</script>""",
    re.IGNORECASE,
)


class ModelsDevFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelsDevBundle:
    models: dict[str, Any]
    providers: dict[str, Any]
    labs: object


async def _get_required_json(client: httpx.AsyncClient, url: str, label: str) -> object:
    try:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        raise ModelsDevFetchError(f"获取 models.dev {label}失败：{exc}") from exc


def parse_labs_page(html: str) -> list[dict[str, Any]]:
    """Read the public lab search index without depending on page rendering."""
    match = _LAB_SEARCH_INDEX.search(html or "")
    if match is None:
        return []
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [
        dict(item)
        for item in payload
        if isinstance(item, dict) and item.get("type") == "lab"
    ]


async def _get_optional_labs(client: httpx.AsyncClient, url: str) -> object:
    try:
        response = await client.get(url)
        response.raise_for_status()
        return parse_labs_page(response.text)
    except Exception:
        # Lab ids and labels can be derived from canonical model ids. The page
        # only enriches descriptions and is intentionally not a refresh blocker.
        return []


async def fetch_models_dev_bundle(
    *,
    client: httpx.AsyncClient | None = None,
    catalog_url: str = DEFAULT_MODELS_DEV_CATALOG_URL,
    labs_url: str = DEFAULT_MODELS_DEV_LABS_URL,
) -> ModelsDevBundle:
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    try:
        catalog, labs = await asyncio.gather(
            _get_required_json(client, catalog_url, "组合目录"),
            _get_optional_labs(client, labs_url),
        )
        if not isinstance(catalog, dict):
            raise ModelsDevFetchError("models.dev 组合目录必须是 JSON 对象")
        models = catalog.get("models")
        providers = catalog.get("providers")
        if not isinstance(models, dict):
            raise ModelsDevFetchError("models.dev 组合目录缺少 models 对象")
        if not isinstance(providers, dict):
            raise ModelsDevFetchError("models.dev 组合目录缺少 providers 对象")
        return ModelsDevBundle(models=models, providers=providers, labs=labs)
    finally:
        if owns_client:
            await client.aclose()
