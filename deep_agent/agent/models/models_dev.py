"""models.dev metadata client (online refresh with offline snapshot fallback)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from deep_agent.agent.models.model_lookup import (
    build_model_lookup_keys,
    expand_provider_aliases,
    infer_providers_from_base_url,
)

logger = logging.getLogger(__name__)

_SNAPSHOT_PATH = Path(__file__).parent / "models_dev_snapshot.json"
_snapshot_cache: dict[str, dict[str, Any]] | None = None
_catalog_cache: dict[str, Any] | None = None

DEFAULT_MODELS_DEV_CATALOG_URL = "https://models.dev/api.json"
_MODALITY_KEYS = ("text", "image", "audio", "video", "pdf")


def _load_snapshot() -> dict[str, dict[str, Any]]:
    global _snapshot_cache
    if _snapshot_cache is None:
        try:
            _snapshot_cache = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load models.dev snapshot: {}", exc)
            _snapshot_cache = {}
    return _snapshot_cache


def _extract_modalities(payload: dict[str, Any]) -> list[str] | None:
    modalities = payload.get("input_modalities")
    if isinstance(modalities, list) and modalities:
        return [str(item) for item in modalities]

    nested = payload.get("modalities")
    if isinstance(nested, dict):
        input_modalities = nested.get("input")
        if isinstance(input_modalities, list) and input_modalities:
            return [str(item) for item in input_modalities]
        resolved = [key for key in _MODALITY_KEYS if nested.get(key)]
        if resolved:
            return resolved

    legacy = payload.get("input")
    if isinstance(legacy, list) and legacy:
        return [str(item) for item in legacy]
    return None


def _provider_entry(catalog: dict[str, Any], provider: str) -> dict[str, Any] | None:
    entry = catalog.get(provider)
    return entry if isinstance(entry, dict) else None


def _model_entry(catalog: dict[str, Any], provider: str, model_id: str) -> dict[str, Any] | None:
    provider_data = _provider_entry(catalog, provider)
    if provider_data is None:
        return None
    models = provider_data.get("models")
    if not isinstance(models, dict):
        return None
    model_data = models.get(model_id)
    return model_data if isinstance(model_data, dict) else None


def _lookup_provider_model(
    catalog: dict[str, Any],
    provider: str,
    model_id: str,
) -> list[str] | None:
    for candidate_provider in expand_provider_aliases(provider):
        model_data = _model_entry(catalog, candidate_provider, model_id)
        if model_data is None:
            continue
        modalities = _extract_modalities(model_data)
        if modalities:
            return modalities
    return None


def _lookup_bare_model(
    catalog: dict[str, Any],
    model_id: str,
    *,
    preferred_providers: list[str],
) -> list[str] | None:
    providers_to_try: list[str] = []
    seen: set[str] = set()

    def add(provider: str) -> None:
        for alias in expand_provider_aliases(provider):
            if alias not in seen:
                seen.add(alias)
                providers_to_try.append(alias)

    for provider in preferred_providers:
        add(provider)
    for provider in catalog:
        if isinstance(provider, str):
            add(provider)

    for provider in providers_to_try:
        modalities = _lookup_provider_model(catalog, provider, model_id)
        if modalities:
            return modalities
    return None


def lookup_modalities_in_catalog(
    catalog: dict[str, Any],
    model: str,
    base_url: str | None = None,
) -> list[str] | None:
    """Resolve modalities from a models.dev ``api.json`` catalog payload."""
    preferred_providers = infer_providers_from_base_url(base_url)
    candidates = build_model_lookup_keys(model, base_url)

    for key in candidates:
        if "/" not in key:
            continue
        provider, model_id = key.split("/", 1)
        modalities = _lookup_provider_model(catalog, provider, model_id)
        if modalities:
            return modalities

    bare_names = [key for key in candidates if "/" not in key and ":" not in key]
    for bare in bare_names:
        modalities = _lookup_bare_model(
            catalog,
            bare,
            preferred_providers=preferred_providers,
        )
        if modalities:
            return modalities
    return None


def _lookup_snapshot(model: str, base_url: str | None = None) -> list[str] | None:
    snapshot = _load_snapshot()
    for key in build_model_lookup_keys(model, base_url):
        entry = snapshot.get(key)
        if isinstance(entry, dict):
            modalities = _extract_modalities(entry)
            if modalities:
                return modalities
    return None


async def fetch_models_dev_catalog(
    *,
    client: httpx.AsyncClient | None = None,
    catalog_url: str = DEFAULT_MODELS_DEV_CATALOG_URL,
    use_cache: bool = True,
) -> dict[str, Any] | None:
    """Download the models.dev provider catalog (``api.json``)."""
    global _catalog_cache
    if use_cache and _catalog_cache is not None:
        return _catalog_cache

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)

    try:
        response = await client.get(catalog_url)
        response.raise_for_status()
        catalog = response.json()
        if not isinstance(catalog, dict):
            logger.warning("models.dev catalog payload is not an object")
            return None
        if use_cache:
            _catalog_cache = catalog
        return catalog
    except Exception as exc:
        logger.warning("Failed to fetch models.dev catalog: {}", exc)
        return None
    finally:
        if owns_client and client is not None:
            await client.aclose()


async def fetch_models_dev_modalities(
    model: str,
    *,
    base_url: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[str] | None:
    """Fetch modalities from models.dev; fall back to bundled snapshot file."""
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)

    try:
        catalog = await fetch_models_dev_catalog(client=client)
        if catalog is not None:
            modalities = lookup_modalities_in_catalog(catalog, model, base_url)
            if modalities:
                return modalities
    finally:
        if owns_client and client is not None:
            await client.aclose()

    return _lookup_snapshot(model, base_url)


def clear_models_dev_cache() -> None:
    """Reset in-memory catalog cache (mainly for tests)."""
    global _catalog_cache
    _catalog_cache = None
