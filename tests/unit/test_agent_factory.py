from __future__ import annotations

import os
import unittest
from unittest import mock

from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider

from deep_agent.agent.factory import _resolve_model, _toolsets_for_spec, build_agent
from deep_agent.agent.models import DeepSeekChatModel, OpenAICompatibleVideoChatModel
from deep_agent.agent.models.deepseek_thinking_retry import DeepSeekThinkingRetry
from deep_agent.capabilities.registry import ToolPackage, ToolPackageRegistry
from deep_agent.agent.spec import AgentSpec
from deep_agent.options import OPTION


def _spec(**kwargs: object) -> AgentSpec:
    defaults = {
        "name": "test",
        "model": "openai:gpt-4o",
        "instructions": "hi",
        "capabilities": None,
        "base_url": None,
        "api_key": None,
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
        self.assertEqual(model.profile["openai_chat_thinking_field"], "reasoning_content")
        self.assertEqual(model.profile["openai_chat_send_back_thinking_parts"], "field")

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

    def test_openai_prefixed_model_with_api_key_uses_explicit_provider(self) -> None:
        resolved = _resolve_model(_spec(model="openai:gpt-4o", api_key="sk-test"))
        self.assertIsInstance(resolved, OpenAIChatModel)
        self.assertIsInstance(resolved._provider, OpenAIProvider)
        self.assertEqual(resolved.model_name, "gpt-4o")

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

    def test_openai_compatible_video_modality_uses_video_adapter(self) -> None:
        model = _resolve_model(
            _spec(
                model="private:video-model",
                api_key="sk-test",
                base_url="https://api.example.com/v1",
                input_modalities=["text", "video"],
            ),
        )
        self.assertIsInstance(model, OpenAICompatibleVideoChatModel)

    def test_openai_compatible_video_tag_uses_video_adapter(self) -> None:
        model = _resolve_model(
            _spec(
                model="private:video-model",
                api_key="sk-test",
                base_url="https://api.example.com/v1",
                tags=["video_url"],
            ),
        )
        self.assertIsInstance(model, OpenAICompatibleVideoChatModel)

    def test_prefixed_model_without_api_key_returns_string(self) -> None:
        resolved = _resolve_model(_spec(model="openai:gpt-4o"))
        self.assertEqual(resolved, "openai:gpt-4o")

    def test_deepseek_without_spec_api_key_uses_deepseek_env_key(self) -> None:
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-env"}, clear=False):
            model = _resolve_model(_spec(model="deepseek:deepseek-v4-flash"))
        self.assertIsInstance(model, DeepSeekChatModel)
        self.assertIsInstance(model._provider, DeepSeekProvider)

    def test_gemini_without_prefix_returns_string_without_google_prefix(self) -> None:
        resolved = _resolve_model(
            _spec(model="gemini-3.5-flash", api_key="google-test-key"),
        )
        self.assertEqual(resolved, "gemini-3.5-flash")

    def test_google_prefixed_model_uses_google_provider_with_spec_api_key(self) -> None:
        model = _resolve_model(
            _spec(model="google:gemini-2.5-flash", api_key="google-test-key"),
        )
        self.assertIsInstance(model, GoogleModel)
        self.assertIsInstance(model._provider, GoogleProvider)
        self.assertEqual(model.model_name, "gemini-2.5-flash")

    def test_google_without_spec_api_key_uses_google_env_key(self) -> None:
        with mock.patch.dict(os.environ, {"GOOGLE_API_KEY": "google-env"}, clear=False):
            model = _resolve_model(_spec(model="google:gemini-2.5-flash"))
        self.assertIsInstance(model, GoogleModel)
        self.assertIsInstance(model._provider, GoogleProvider)

    def test_google_with_gemini_proxy_uses_dedicated_http_client(self) -> None:
        prev = OPTION.agent.gemini_proxy
        try:
            OPTION.agent.gemini_proxy = "http://127.0.0.1:7890"
            model = _resolve_model(
                _spec(model="google:gemini-2.5-flash", api_key="google-test-key"),
            )
            client = model._provider._client._api_client._async_httpx_client
            self.assertFalse(client._trust_env)
            transport = list(client._mounts.values())[0]
            self.assertIn("127.0.0.1", str(transport._pool._proxy_url))
        finally:
            OPTION.agent.gemini_proxy = prev

    def test_deepseek_registers_thinking_retry_capability(self) -> None:
        agent = build_agent(
            _spec(model="deepseek:deepseek-v4-flash", api_key="sk-test"),
            ToolPackageRegistry(),
        )
        caps = [c for c in agent.root_capability.capabilities if isinstance(c, DeepSeekThinkingRetry)]
        self.assertEqual(len(caps), 1)

    def test_non_deepseek_has_no_thinking_retry_capability(self) -> None:
        agent = build_agent(
            _spec(
                model="deepseek:deepseek-v4-flash",
                api_key="sk-test",
                base_url="https://gateway.example.com/v1",
            ),
            ToolPackageRegistry(),
        )
        caps = [c for c in agent.root_capability.capabilities if isinstance(c, DeepSeekThinkingRetry)]
        self.assertEqual(len(caps), 0)


class ToolsetAssemblyTests(unittest.TestCase):
    def test_selected_packages_preserve_all_runtime_toolset_layers(self) -> None:
        registry = ToolPackageRegistry()
        general = object()
        sandbox = object()
        attachment = object()
        for code, toolset in (
            ("builtin:general", general),
            ("builtin:sandbox", sandbox),
            ("builtin:attachment", attachment),
        ):
            registry.register(
                ToolPackage(
                    code=code,
                    category="builtin",
                    toolset=toolset,
                    default_enabled=code == "builtin:general",
                )
            )

        mcp = object()
        resources = object()
        ui = object()
        spec = _spec(
            capabilities=[
                "builtin:general",
                "builtin:sandbox",
                "builtin:attachment",
                "mcp:deepcube",
                "ui:deepcube",
            ]
        )
        assembled = _toolsets_for_spec(
            registry,
            spec,
            mcp_toolsets=[mcp],
            mcp_resource_toolset=resources,
            dynamic_ui_packages=[
                ToolPackage(
                    code="ui:deepcube",
                    category="ui",
                    toolset=ui,
                    default_enabled=False,
                )
            ],
        )

        self.assertEqual(
            assembled,
            [general, sandbox, attachment, mcp, resources, ui],
        )

    def test_null_and_empty_selection_keep_original_default_semantics(self) -> None:
        registry = ToolPackageRegistry()
        general = object()
        attachment = object()
        registry.register(
            ToolPackage(
                code="builtin:general",
                category="builtin",
                toolset=general,
                default_enabled=True,
            )
        )
        registry.register(
            ToolPackage(
                code="builtin:attachment",
                category="builtin",
                toolset=attachment,
                default_enabled=False,
            )
        )

        self.assertEqual(_toolsets_for_spec(registry, _spec(capabilities=None)), [general])
        self.assertEqual(_toolsets_for_spec(registry, _spec(capabilities=[])), [])


if __name__ == "__main__":
    unittest.main()
