"""Test-only models.dev snapshot responses."""

from __future__ import annotations

MODELS_DEV_SNAPSHOT: dict[str, dict[str, list[str]]] = {
    "openai/gpt-4o-online": {"input_modalities": ["text", "image", "audio"]},
    "openai:online-only-model": {"input_modalities": ["text", "image"]},
}


async def mock_fetch_models_dev_modalities(model: str, **kwargs) -> list[str] | None:
    for key, entry in MODELS_DEV_SNAPSHOT.items():
        if key == model or key.endswith(model) or model.endswith(key.split("/")[-1]):
            return list(entry["input_modalities"])
    if model in {"openai:online-only-model", "online-only-model"}:
        return ["text", "image"]
    if model in {"openai/gpt-4o-online", "gpt-4o-online"}:
        return ["text", "image", "audio"]
    return None
