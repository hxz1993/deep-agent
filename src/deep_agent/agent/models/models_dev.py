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


class ModelsDevCatalogError(ValueError):
    pass


@dataclass(frozen=True)
class ModelsDevBundle:
    models: dict[str, Any]
    providers: dict[str, Any]
    labs: object


def parse_models_dev_catalog(payload: bytes | str | object) -> ModelsDevBundle:
    """Parse the official combined catalog used by online refresh and JSON import."""
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ModelsDevCatalogError("models.dev 目录JSON必须使用UTF-8编码") from exc
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ModelsDevCatalogError(
                f"models.dev 目录JSON格式错误：第{exc.lineno}行第{exc.colno}列"
            ) from exc
    if not isinstance(payload, dict):
        raise ModelsDevCatalogError("models.dev 组合目录必须是JSON对象")
    models = payload.get("models")
    providers = payload.get("providers")
    if not isinstance(models, dict):
        raise ModelsDevCatalogError("models.dev 组合目录缺少models对象")
    if not isinstance(providers, dict):
        raise ModelsDevCatalogError("models.dev 组合目录缺少providers对象")
    return ModelsDevBundle(
        models=models,
        providers=providers,
        labs=payload.get("labs", []),
    )


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
        try:
            bundle = parse_models_dev_catalog(catalog)
        except ModelsDevCatalogError as exc:
            raise ModelsDevFetchError(str(exc)) from exc
        return ModelsDevBundle(
            models=bundle.models,
            providers=bundle.providers,
            labs=labs,
        )
    finally:
        if owns_client:
            await client.aclose()
