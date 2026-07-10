"""Model input modality resolver.

Resolves supported input modalities for a model using a layered approach:
1. User override (DB field)
2. Bundled snapshot (offline fallback)
3. Name heuristic (last resort)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from deep_agent.agent.models.model_lookup import build_model_lookup_keys

if TYPE_CHECKING:
    from deep_agent.db.stores.llm_model.base import LlmModelStoreBase

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).parent / "modality_data.json"
_bundle_cache: dict[str, dict] | None = None


def _load_bundle() -> dict[str, dict]:
    """Load bundled modality data from JSON file."""
    global _bundle_cache
    if _bundle_cache is None:
        try:
            _bundle_cache = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load bundled modality data: %s", exc)
            _bundle_cache = {}
    return _bundle_cache


def _build_candidate_keys(model: str, base_url: str | None = None) -> list[str]:
    return build_model_lookup_keys(model, base_url)


def _name_heuristic(model: str) -> list[str]:
    """Fallback heuristic based on model name patterns."""
    name = model.lower()

    # Vision-capable patterns (order matters - more specific first)
    vision_markers = (
        "gpt-4o",
        "gpt-4-turbo",
        "gpt-4-vision",
        "gpt-4.1",
        "gpt-5",
        "claude-3",
        "claude-sonnet-4",
        "claude-opus-4",
        "gemini",
        "qwen3-vl",
        "qwen3.7",
        "qwen3.6",
        "qwen3.5-omni",
        "deepseek-vl",
        "pixtral",
        "grok-2",
        "grok-3",
        "moonshot-v1-vision",
        "moonshot-v1",
        "kimi-k2",
        "kimi-k2.",
        "glm-4v",
        "internvl",
    )
    has_vision = any(marker in name for marker in vision_markers)

    # Audio-capable patterns
    audio_markers = ("whisper", "tts", "audio")
    has_audio = any(marker in name for marker in audio_markers)

    modalities = ["text"]
    if has_vision:
        modalities.append("image")
    if has_audio:
        modalities.append("audio")

    return modalities


def _resolve_from_bundle(model: str, base_url: str | None = None) -> list[str] | None:
    bundle = _load_bundle()
    for key in _build_candidate_keys(model, base_url):
        entry = bundle.get(key)
        if entry and "input_modalities" in entry:
            return entry["input_modalities"]
    return None


def resolve_input_modalities(
    model: str,
    *,
    user_override: list[str] | None = None,
    capabilities_source: str | None = None,
    base_url: str | None = None,
) -> list[str]:
    """Resolve supported input modalities for a model.

    Resolution order:
    1. DB override (user / models_dev / bundled sources)
    2. Bundled snapshot lookup
    3. Name heuristic fallback

    Args:
        model: Model string (e.g., 'openai:gpt-4o' or 'gpt-4o')
        user_override: Modalities persisted in DB
        capabilities_source: Source of the DB data ('user', 'models_dev', etc.)
        base_url: Optional endpoint URL used to infer provider for bare model names

    Returns:
        List of supported input modalities (e.g., ['text', 'image'])
    """
    # 1. DB persisted values (priority depends on source)
    if user_override is not None and capabilities_source in {
        "user",
        "models_dev",
        "bundled",
    }:
        return user_override

    # 2. Bundled snapshot lookup
    bundled = _resolve_from_bundle(model, base_url)
    if bundled is not None:
        return bundled

    # 3. Name heuristic (fallback)
    return _name_heuristic(model)


async def refresh_model_modalities(
    model_id: str,
    store: LlmModelStoreBase,
    *,
    force: bool = False,
) -> list[str] | None:
    """Refresh modalities for a model from models.dev, then bundled/heuristic."""
    row = await store.get_by_id(model_id)
    if row is None:
        return None

    # Skip if user has manually set modalities (unless forced)
    if not force and row.capabilities_source == "user":
        return row.input_modalities

    from deep_agent.agent.models.models_dev import fetch_models_dev_modalities

    modalities = await fetch_models_dev_modalities(row.model, base_url=row.base_url)
    source = "models_dev"
    if modalities is None:
        bundled = _resolve_from_bundle(row.model, row.base_url)
        if bundled is not None:
            modalities = bundled
            source = "bundled"
        else:
            modalities = _name_heuristic(row.model)
            source = "heuristic"

    await store.update_metadata(
        model_id,
        input_modalities=modalities,
        capabilities_source=source,
    )

    logger.info(
        "Refreshed modalities for %s: %s (source=%s)",
        row.name,
        modalities,
        source,
    )
    return modalities


async def refresh_all_model_modalities(
    store: LlmModelStoreBase,
    *,
    force: bool = False,
) -> int:
    """Batch refresh modalities for all models.

    Returns count of updated models.
    """
    rows = await store.list_all()
    updated = 0
    for row in rows:
        result = await refresh_model_modalities(row.id, store, force=force)
        if result is not None:
            updated += 1
    return updated
