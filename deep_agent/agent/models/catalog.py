"""Browseable model modality catalog (models.dev + bundled JSON)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from deep_agent.agent.models.models_dev import (
    _extract_modalities,
    fetch_models_dev_catalog,
)

logger = logging.getLogger(__name__)

_BUNDLE_PATH = Path(__file__).parent / "modality_data.json"
_SNAPSHOT_PATH = Path(__file__).parent / "models_dev_snapshot.json"

CatalogSource = Literal["auto", "online", "bundled"]

_SOURCE_LABELS = {
    "models_dev": "models.dev（在线）",
    "bundled": "内置 modality_data.json",
    "snapshot": "models_dev_snapshot.json",
}

_bundled_entries_cache: list["CatalogEntry"] | None = None
_provider_parsed_cache: dict[str, list["CatalogEntry"]] = {}


@dataclass(frozen=True)
class CatalogEntry:
    key: str
    provider: str
    model_id: str
    provider_label: str | None
    input_modalities: list[str]
    max_context: int | None
    source: str


def clear_model_catalog_cache() -> None:
    """Reset parsed catalog cache (tests / manual refresh)."""
    global _bundled_entries_cache
    _bundled_entries_cache = None
    _provider_parsed_cache.clear()


def _extract_max_context(payload: dict[str, Any]) -> int | None:
    limit = payload.get("limit")
    if isinstance(limit, dict):
        ctx = limit.get("context")
        if isinstance(ctx, int) and ctx > 0:
            return ctx
    for key in ("max_context", "context_length", "context"):
        value = payload.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None


def _parse_key(key: str) -> tuple[str, str]:
    if "/" in key:
        provider, model_id = key.split("/", 1)
        return provider, model_id
    return "", key


def _entry_from_flat_key(key: str, payload: dict[str, Any], *, source: str) -> CatalogEntry | None:
    modalities = payload.get("input_modalities")
    if not isinstance(modalities, list) or not modalities:
        modalities = _extract_modalities(payload)
    if not modalities:
        modalities = ["text"]
    provider, model_id = _parse_key(key)
    return CatalogEntry(
        key=key if "/" in key else f"{provider}/{model_id}" if provider else model_id,
        provider=provider,
        model_id=model_id,
        provider_label=provider or None,
        input_modalities=[str(m) for m in modalities],
        max_context=_extract_max_context(payload),
        source=source,
    )


def _load_json_map(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load catalog file %s: %s", path, exc)
        return {}
    return raw if isinstance(raw, dict) else {}


def _get_bundled_entries() -> list[CatalogEntry]:
    global _bundled_entries_cache
    if _bundled_entries_cache is not None:
        return _bundled_entries_cache

    entries: list[CatalogEntry] = []
    seen: set[str] = set()

    for key, payload in _load_json_map(_BUNDLE_PATH).items():
        if not isinstance(payload, dict):
            continue
        entry = _entry_from_flat_key(str(key), payload, source="bundled")
        if entry is None:
            continue
        norm = entry.key.lower()
        if norm in seen:
            continue
        seen.add(norm)
        entries.append(entry)

    for key, payload in _load_json_map(_SNAPSHOT_PATH).items():
        if not isinstance(payload, dict):
            continue
        entry = _entry_from_flat_key(str(key), payload, source="snapshot")
        if entry is None:
            continue
        norm = entry.key.lower()
        if norm in seen:
            continue
        seen.add(norm)
        entries.append(entry)

    entries.sort(key=lambda e: (e.provider.lower(), e.model_id.lower()))
    _bundled_entries_cache = entries
    return entries


def iter_bundled_entries() -> list[CatalogEntry]:
    return _get_bundled_entries()


def list_models_dev_provider_ids(catalog: dict[str, Any]) -> list[str]:
    """List provider ids from raw models.dev catalog without parsing model entries."""
    return sorted(
        [
            str(provider_id)
            for provider_id, provider_data in catalog.items()
            if isinstance(provider_data, dict) and isinstance(provider_data.get("models"), dict)
        ],
        key=str.lower,
    )


def _find_provider_key(catalog: dict[str, Any], provider: str) -> str | None:
    norm = provider.strip().lower()
    for key in catalog:
        if str(key).lower() == norm:
            return str(key)
    return None


def iter_models_dev_provider_entries(catalog: dict[str, Any], provider_id: str) -> list[CatalogEntry]:
    provider_data = catalog.get(provider_id)
    if not isinstance(provider_data, dict):
        return []
    models = provider_data.get("models")
    if not isinstance(models, dict):
        return []

    provider_label = provider_data.get("name")
    label = str(provider_label) if provider_label else str(provider_id)
    entries: list[CatalogEntry] = []
    for model_id, model_data in models.items():
        if not isinstance(model_data, dict):
            continue
        modalities = _extract_modalities(model_data) or ["text"]
        entries.append(
            CatalogEntry(
                key=f"{provider_id}/{model_id}",
                provider=str(provider_id),
                model_id=str(model_id),
                provider_label=label,
                input_modalities=modalities,
                max_context=_extract_max_context(model_data),
                source="models_dev",
            )
        )
    entries.sort(key=lambda e: e.model_id.lower())
    return entries


def get_models_dev_entries(catalog: dict[str, Any], *, provider: str | None = None) -> list[CatalogEntry]:
    """Parse models.dev catalog entries with per-provider lazy cache."""
    if provider:
        provider_key = _find_provider_key(catalog, provider)
        if provider_key is None:
            return []
        if provider_key not in _provider_parsed_cache:
            _provider_parsed_cache[provider_key] = iter_models_dev_provider_entries(catalog, provider_key)
        return list(_provider_parsed_cache[provider_key])

    all_entries: list[CatalogEntry] = []
    for provider_id in list_models_dev_provider_ids(catalog):
        if provider_id not in _provider_parsed_cache:
            _provider_parsed_cache[provider_id] = iter_models_dev_provider_entries(catalog, provider_id)
        all_entries.extend(_provider_parsed_cache[provider_id])
    all_entries.sort(key=lambda e: (e.provider.lower(), e.model_id.lower()))
    return all_entries


def iter_models_dev_entries(catalog: dict[str, Any]) -> list[CatalogEntry]:
    return get_models_dev_entries(catalog)


async def load_catalog_entries(
    source: CatalogSource = "auto",
    *,
    provider: str | None = None,
    refresh: bool = False,
) -> tuple[list[CatalogEntry], str, list[str]]:
    """Load catalog entries. Returns (entries, resolved_source_key, provider_ids)."""
    if refresh:
        clear_model_catalog_cache()
        from deep_agent.agent.models.models_dev import clear_models_dev_cache

        clear_models_dev_cache()

    if source == "bundled":
        entries = _get_bundled_entries()
        provider_ids = distinct_providers(entries)
        if provider:
            provider_norm = provider.strip().lower()
            entries = [e for e in entries if e.provider.lower() == provider_norm]
        return entries, "bundled", provider_ids

    catalog = await fetch_models_dev_catalog(use_cache=not refresh)
    if catalog is not None:
        provider_ids = list_models_dev_provider_ids(catalog)
        entries = get_models_dev_entries(catalog, provider=provider)
        return entries, "models_dev", provider_ids

    entries = _get_bundled_entries()
    provider_ids = distinct_providers(entries)
    if provider:
        provider_norm = provider.strip().lower()
        entries = [e for e in entries if e.provider.lower() == provider_norm]
    return entries, "bundled", provider_ids


def filter_catalog_entries(
    entries: list[CatalogEntry],
    *,
    q: str | None = None,
    provider: str | None = None,
    modality: str | None = None,
) -> list[CatalogEntry]:
    q_norm = (q or "").strip().lower()
    provider_norm = (provider or "").strip().lower()
    modality_norm = (modality or "").strip().lower()

    filtered: list[CatalogEntry] = []
    for entry in entries:
        if provider_norm and entry.provider.lower() != provider_norm:
            continue
        if modality_norm:
            mods = {m.lower() for m in entry.input_modalities}
            if modality_norm not in mods:
                continue
        if q_norm:
            hay = " ".join(
                [
                    entry.key,
                    entry.provider,
                    entry.model_id,
                    entry.provider_label or "",
                    " ".join(entry.input_modalities),
                ]
            ).lower()
            if q_norm not in hay:
                continue
        filtered.append(entry)
    return filtered


def paginate_catalog(
    entries: list[CatalogEntry],
    *,
    offset: int = 0,
    limit: int = 100,
) -> tuple[list[CatalogEntry], int]:
    offset = max(0, offset)
    limit = max(1, min(limit, 500))
    total = len(entries)
    return entries[offset : offset + limit], total


def catalog_source_label(resolved: str) -> str:
    return _SOURCE_LABELS.get(resolved, resolved)


def distinct_providers(entries: list[CatalogEntry]) -> list[str]:
    providers = sorted({e.provider for e in entries if e.provider}, key=str.lower)
    return providers


def normalize_model_lookup_key(model: str) -> str:
    """Normalize provider/model or provider:model for catalog matching."""
    s = (model or "").strip().lower()
    if ":" in s and "/" not in s:
        s = s.replace(":", "/", 1)
    return s


def build_configured_preset_map(presets: list[tuple[str, str]]) -> dict[str, str]:
    """Map normalized full model key (provider/model) -> preset display name."""
    out: dict[str, str] = {}
    for name, model in presets:
        key = normalize_model_lookup_key(model)
        if not key or "/" not in key:
            continue
        if key not in out:
            out[key] = name
    return out


def lookup_configured_preset(preset_map: dict[str, str], entry: CatalogEntry) -> str | None:
    """Match configured preset by full provider/model key only."""
    return preset_map.get(entry.key.lower())
