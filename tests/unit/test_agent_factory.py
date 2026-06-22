from __future__ import annotations

import os
import unittest
from unittest import mock

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.openai import OpenAIProvider

from deep_agent.agent.factory import _resolve_model
from deep_agent.agent.models import DeepSeekChatModel
from deep_agent.agent.spec import AgentSpec


def _spec(**kwargs: object) -> AgentSpec:
    defaults = {
        "name": "test",
        "model": "openai:gpt-4o",
        "instructions": "hi",
        "capabilities": None,
        "base_url": None,
        "api_key": None,
        "max_requests": None,
    }
    defaults.update(kwargs)
    return AgentSpec(**defaults)  # type: ignore[arg-type]


class ResolveModelTests(unittest.TestCase):
    def test_deepseek_uses_deepseek_provider_with_spec_api_key(self) -> None:
        model = _resolve_model(
            _spec(model="deepseek:deepseek-v4-flash", api_key="sk-test"),
        )
        self.assertIsInstance(model, DeepSeekChatModel)
        self.assertIsInstance(model._provider, DeepSeekProvider)
        profile = OpenAIModelProfile.from_profile(model.profile)
        self.assertEqual(profile.openai_chat_thinking_field, "reasoning_content")
        self.assertEqual(profile.openai_chat_send_back_thinking_parts, "field")

    def test_deepseek_with_matching_base_url_uses_deepseek_provider(self) -> None:
        model = _resolve_model(
            _spec(
                model="deepseek:deepseek-v4-flash",
                api_key="sk-test",
                base_url="https://api.deepseek.com/",
            ),
        )
        self.assertIsInstance(model, DeepSeekChatModel)
        self.assertIsInstance(model._provider, DeepSeekProvider)

    def test_deepseek_with_custom_base_url_falls_back_to_openai_provider(self) -> None:
        model = _resolve_model(
            _spec(
                model="deepseek:deepseek-v4-flash",
                api_key="sk-test",
                base_url="https://gateway.example.com/v1",
            ),
        )
        self.assertIsInstance(model, OpenAIChatModel)
        self.assertIsInstance(model._provider, OpenAIProvider)
        self.assertEqual(model.model_name, "deepseek-v4-flash")
        self.assertEqual(
            model._provider.base_url.rstrip("/"),
            "https://gateway.example.com/v1",
        )

    def test_non_deepseek_with_base_url_uses_openai_provider(self) -> None:
        model = _resolve_model(
            _spec(
                model="deepseek-v4-flash",
                api_key="sk-test",
                base_url="https://api.example.com/v1",
            ),
        )
        self.assertIsInstance(model, OpenAIChatModel)
        self.assertIsInstance(model._provider, OpenAIProvider)
        self.assertEqual(model._provider.base_url.rstrip("/"), "https://api.example.com/v1")

    def test_non_deepseek_prefixed_model_with_api_key_returns_string(self) -> None:
        resolved = _resolve_model(_spec(model="openai:gpt-4o", api_key="sk-test"))
        self.assertEqual(resolved, "openai:gpt-4o")

    def test_non_deepseek_prefixed_model_with_base_url_uses_openai_provider(self) -> None:
        model = _resolve_model(
            _spec(
                model="openai:gpt-4o",
                api_key="sk-test",
                base_url="https://api.example.com/v1",
            ),
        )
        self.assertIsInstance(model, OpenAIChatModel)
        self.assertIsInstance(model._provider, OpenAIProvider)
        self.assertEqual(model.model_name, "gpt-4o")

    def test_prefixed_model_without_api_key_returns_string(self) -> None:
        resolved = _resolve_model(_spec(model="openai:gpt-4o"))
        self.assertEqual(resolved, "openai:gpt-4o")

    def test_deepseek_without_spec_api_key_uses_deepseek_env_key(self) -> None:
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-env"}, clear=False):
            model = _resolve_model(_spec(model="deepseek:deepseek-v4-flash"))
        self.assertIsInstance(model, DeepSeekChatModel)
        self.assertIsInstance(model._provider, DeepSeekProvider)


if __name__ == "__main__":
    unittest.main()
