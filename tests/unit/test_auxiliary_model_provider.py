from __future__ import annotations

import unittest
from types import SimpleNamespace

from deep_agent.api.v1.auxiliary_models import _response_for_row


class _ModelStore:
    def __init__(self, model) -> None:
        self.model = model

    async def get_by_id(self, model_id: str):
        return self.model if model_id == self.model.id else None


class _ConfigurationService:
    def __init__(self, provider) -> None:
        self.provider = provider

    async def get_provider(self, provider_id: str):
        return self.provider if provider_id == self.provider.id else None


def _model():
    return SimpleNamespace(
        id="model-1",
        provider_id="provider-1",
        name="Dedicated title model",
        model="deepseek-chat",
    )


def _provider(*, enabled: bool = True):
    return SimpleNamespace(
        id="provider-1",
        name="DeepSeek",
        provider_type="deepseek",
        use_default_base_url=False,
        base_url="https://provider.example/v1",
        api_key="provider-key",
        enabled=enabled,
    )


class AuxiliaryModelProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_response_uses_linked_provider_compatibility_fields(self) -> None:
        model = _model()
        response = await _response_for_row(
            "title",
            SimpleNamespace(llm_model_id=model.id, updated_at=None),
            _ModelStore(model),
            _ConfigurationService(_provider()),
        )

        self.assertEqual(response.base_url, "https://provider.example/v1")
        self.assertTrue(response.api_key_set)
        self.assertFalse(hasattr(model, "base_url"))

    async def test_disabled_provider_remains_visible_in_configuration_response(self) -> None:
        model = _model()
        response = await _response_for_row(
            "title",
            SimpleNamespace(llm_model_id=model.id, updated_at=None),
            _ModelStore(model),
            _ConfigurationService(_provider(enabled=False)),
        )

        self.assertEqual(response.base_url, "https://provider.example/v1")
        self.assertTrue(response.api_key_set)


if __name__ == "__main__":
    unittest.main()
