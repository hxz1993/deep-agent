from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.alibaba import AlibabaProvider
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.moonshotai import MoonshotAIProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.providers.zai import ZaiProvider

from deep_agent.models import OpenAICompatibleVideoChatModel
from deep_agent.models.compatible_providers import MiniMaxProvider, StepFunProvider
from deep_agent.models.provider_registry import (
    PROVIDER_TYPES,
    ProviderConfigurationError,
    ProviderRuntimeConfig,
    UnknownProviderTypeError,
    effective_base_url,
    load_provider_for_model,
    model_unavailable_reason,
    provider_type_definition,
    resolve_provider_model,
    validate_provider_config,
)


def _config(
    provider_type: str,
    *,
    use_default_base_url: bool = True,
    base_url: str = "https://gateway.example.com/v1/",
    api_key: str = "sk-test",
) -> ProviderRuntimeConfig:
    return ProviderRuntimeConfig(
        id=f"provider-{provider_type}",
        name=provider_type,
        provider_type=provider_type,
        use_default_base_url=use_default_base_url,
        base_url=base_url,
        api_key=api_key,
    )


class ProviderDefinitionTests(unittest.TestCase):
    def test_thinking_providers_do_not_force_output_tool_on_wire(self) -> None:
        from pydantic_ai.models import ModelRequestParameters
        from pydantic_ai.tools import ToolDefinition
        params = ModelRequestParameters(output_tools=[ToolDefinition(name="result", parameters_json_schema={"type": "object"})],
                                        allow_text_output=False)
        for provider_type, model_name in (("deepseek", "deepseek-flash"), ("deepseek", "deepseek-v4-pro"),
                                          ("moonshotai", "kimi-k2.6")):
            for use_default in (True, False):
                with self.subTest(provider=provider_type, default=use_default):
                    model = resolve_provider_model(model_name=model_name,
                        provider=_config(provider_type, use_default_base_url=use_default))
                    tools, choice = model._get_tool_choice({}, params)
                    self.assertEqual(choice, "auto")
                    self.assertEqual(tools[0]["function"]["name"], "result")
                    self.assertFalse(params.allow_text_output)

    def test_deepseek_output_limit_mapping_preserves_native_profile(self) -> None:
        for use_default in (True, False):
            with self.subTest(use_default=use_default):
                model = resolve_provider_model(model_name="deepseek-v4-pro",
                    provider=_config("deepseek", use_default_base_url=use_default))
                self.assertFalse(model.profile["openai_chat_supports_max_completion_tokens"])
                self.assertTrue(model.profile["supports_thinking"])
                self.assertEqual(model.profile["openai_chat_send_back_thinking_parts"], "field")
        other = resolve_provider_model(model_name="custom-model", provider=_config("openai"))
        self.assertNotEqual(other.profile.get("openai_chat_supports_max_completion_tokens"), False)

    def test_registry_order_and_defaults_are_stable(self) -> None:
        self.assertEqual(
            [item.code for item in PROVIDER_TYPES],
            [
                "deepseek",
                "moonshotai",
                "alibaba",
                "zai",
                "openrouter",
                "openai",
                "anthropic",
                "google",
                "openai_compatible",
                "openai_codex",
                "minimax",
                "stepfun",
                "anthropic_compatible",
            ],
        )
        self.assertEqual(
            [item.order for item in PROVIDER_TYPES], list(range(1, 14))
        )
        self.assertEqual(
            {item.code: item.default_base_url for item in PROVIDER_TYPES},
            {
                "deepseek": "https://api.deepseek.com",
                "moonshotai": "https://api.moonshot.ai/v1/",
                "alibaba": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/",
                "zai": "https://api.z.ai/api/paas/v4/",
                "openrouter": "https://openrouter.ai/api/v1/",
                "openai": "https://api.openai.com/v1/",
                "anthropic": "https://api.anthropic.com",
                "google": "https://generativelanguage.googleapis.com/",
                "openai_compatible": None,
                "openai_codex": "https://chatgpt.com/backend-api/codex",
                "minimax": "https://api.minimax.cn/anthropic",
                "stepfun": "https://api.stepfun.com/v1",
                "anthropic_compatible": None,
            },
        )
        self.assertFalse(
            provider_type_definition("openai_compatible").supports_default_base_url
        )
        self.assertTrue(
            all(item.supports_custom_base_url for item in PROVIDER_TYPES if item.code != "openai_codex")
        )
        self.assertFalse(provider_type_definition("openai_codex").supports_custom_base_url)

    def test_unknown_provider_type_is_not_aliased_or_inferred(self) -> None:
        with self.assertRaises(UnknownProviderTypeError):
            provider_type_definition("kimi")


class ProviderValidationTests(unittest.TestCase):
    def test_effective_model_availability_is_provider_configuration(self) -> None:
        model = SimpleNamespace()
        self.assertIsNone(model_unavailable_reason(model, _config("deepseek")))
        self.assertEqual(
            model_unavailable_reason(model, _config("deepseek", api_key="")),
            "provider_unconfigured",
        )

    def test_disabled_model_is_unavailable_before_provider_validation(self) -> None:
        model = SimpleNamespace(enabled=False)
        self.assertEqual(
            model_unavailable_reason(model, _config("deepseek", api_key="")),
            "model_disabled",
        )

    def test_missing_api_key_fails(self) -> None:
        with self.assertRaisesRegex(ProviderConfigurationError, "API key"):
            validate_provider_config(_config("deepseek", api_key=""))

    def test_openai_compatible_cannot_use_a_default_url(self) -> None:
        for provider_type in ("openai_compatible", "anthropic_compatible"):
            with self.subTest(provider_type=provider_type):
                with self.assertRaisesRegex(
                    ProviderConfigurationError, "has no default base URL"
                ):
                    validate_provider_config(_config(provider_type))

    def test_custom_url_is_required_and_must_be_http(self) -> None:
        with self.assertRaisesRegex(ProviderConfigurationError, "required"):
            validate_provider_config(
                _config("deepseek", use_default_base_url=False, base_url="")
            )
        for invalid in (
            "ftp://gateway.example.com/v1",
            "gateway.example.com/v1",
            "https://user:password@gateway.example.com/v1",
            "https://gateway.example.com:invalid/v1",
            "https://gateway.example.com/v1?token=secret",
            "https://gateway.example.com/v1#fragment",
            "https://gateway.example.com/has whitespace/v1",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ProviderConfigurationError, "HTTP"):
                    validate_provider_config(
                        _config(
                            "deepseek",
                            use_default_base_url=False,
                            base_url=invalid,
                        )
                    )

    def test_effective_url_uses_explicit_default_flag(self) -> None:
        default = _config(
            "deepseek",
            use_default_base_url=True,
            base_url="https://ignored.example.com/v1",
        )
        custom = _config(
            "deepseek",
            use_default_base_url=False,
            base_url="https://gateway.example.com/v1/",
        )
        self.assertEqual(effective_base_url(default), "https://api.deepseek.com")
        self.assertEqual(
            effective_base_url(custom), "https://gateway.example.com/v1"
        )


class ProviderLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_loads_and_validates_explicit_model_provider(self) -> None:
        provider = _config("deepseek")
        store = SimpleNamespace(get_provider=AsyncMock(return_value=provider))
        model = SimpleNamespace(id="model-1", provider_id=provider.id)

        resolved = await load_provider_for_model(model, store)

        self.assertIs(resolved, provider)
        store.get_provider.assert_awaited_once_with(provider.id)

    async def test_disabled_model_fails_before_provider_lookup(self) -> None:
        store = SimpleNamespace(get_provider=AsyncMock())
        model = SimpleNamespace(id="model-1", provider_id="provider-1", enabled=False)

        with self.assertRaisesRegex(ProviderConfigurationError, "disabled"):
            await load_provider_for_model(model, store)
        store.get_provider.assert_not_awaited()

    async def test_missing_provider_link_fails_without_fallback(self) -> None:
        store = SimpleNamespace(get_provider=AsyncMock())
        model = SimpleNamespace(id="model-1", provider_id=None)

        with self.assertRaisesRegex(ProviderConfigurationError, "required"):
            await load_provider_for_model(model, store)
        store.get_provider.assert_not_awaited()

    async def test_missing_provider_row_fails_without_fallback(self) -> None:
        store = SimpleNamespace(get_provider=AsyncMock(return_value=None))
        model = SimpleNamespace(id="model-1", provider_id="provider-missing")

        with self.assertRaisesRegex(ProviderConfigurationError, "not found"):
            await load_provider_for_model(model, store)

class ProviderModelAssemblyTests(unittest.TestCase):
    def test_compatible_vendor_profiles_survive_custom_endpoints(self) -> None:
        for code, name, model_class, provider_class in (
            ("minimax", "MiniMax-M2.7", AnthropicModel, MiniMaxProvider),
            ("stepfun", "step-3.5-flash", OpenAIChatModel, StepFunProvider),
        ):
            for use_default in (True, False):
                with self.subTest(code=code, use_default=use_default):
                    config = _config(code, use_default_base_url=use_default)
                    model = resolve_provider_model(model_name=name, provider=config)
                    self.assertIsInstance(model, model_class)
                    self.assertIsInstance(model._provider, provider_class)
                    self.assertEqual(model._provider.name, code)
                    self.assertEqual(model.deepagent_provider_type, code)
                    self.assertEqual(model._provider.base_url.rstrip("/"), effective_base_url(config))
                    if code == "stepfun":
                        self.assertFalse(model.profile["openai_chat_supports_max_completion_tokens"])
                        self.assertFalse(model.profile["openai_supports_strict_tool_definition"])
                        self.assertEqual(model.profile["openai_chat_send_back_thinking_parts"], "field")
                    else:
                        self.assertFalse(model.profile["supports_json_schema_output"])
                        self.assertFalse(model.profile["supported_native_tools"])

    def test_anthropic_compatible_preserves_explicit_protocol_for_foreign_model_name(self) -> None:
        model = resolve_provider_model(
            model_name="deepseek-v4-flash",
            provider=_config("anthropic_compatible", use_default_base_url=False),
        )
        self.assertIsInstance(model, AnthropicModel)
        self.assertIs(type(model._provider), AnthropicProvider)
        self.assertEqual(model.deepagent_provider_type, "anthropic_compatible")
        self.assertEqual(model._provider.base_url.rstrip("/"), "https://gateway.example.com/v1")

    def test_default_openai_style_types_use_native_providers(self) -> None:
        cases = (
            ("deepseek", "deepseek-chat", DeepSeekProvider),
            ("moonshotai", "moonshot-v1-8k", MoonshotAIProvider),
            ("alibaba", "qwen-plus", AlibabaProvider),
            ("zai", "glm-4.5", ZaiProvider),
            ("openrouter", "openai/gpt-4o", OpenRouterProvider),
            ("openai", "gpt-4o", OpenAIProvider),
        )
        for provider_type, model_name, provider_class in cases:
            with self.subTest(provider_type=provider_type):
                model = resolve_provider_model(
                    model_name=model_name,
                    provider=_config(provider_type),
                )
                self.assertIsInstance(model, OpenAIChatModel)
                self.assertIsInstance(model._provider, provider_class)
                self.assertEqual(model.model_name, model_name)

    def test_custom_branded_openai_endpoints_use_plain_openai_provider(self) -> None:
        for provider_type in ("deepseek", "moonshotai", "zai", "openrouter"):
            with self.subTest(provider_type=provider_type):
                model = resolve_provider_model(
                    model_name="vendor-model",
                    provider=_config(
                        provider_type,
                        use_default_base_url=False,
                    ),
                )
                self.assertIs(type(model._provider), OpenAIProvider)
                self.assertEqual(
                    model._provider.base_url.rstrip("/"),
                    "https://gateway.example.com/v1",
                )

    def test_alibaba_custom_endpoint_keeps_native_provider(self) -> None:
        model = resolve_provider_model(
            model_name="qwen-plus",
            provider=_config("alibaba", use_default_base_url=False),
        )
        self.assertIsInstance(model._provider, AlibabaProvider)
        self.assertEqual(
            model._provider.base_url.rstrip("/"),
            "https://gateway.example.com/v1",
        )

    def test_anthropic_and_google_use_native_models_and_providers(self) -> None:
        anthropic = resolve_provider_model(
            model_name="claude-sonnet-4-5",
            provider=_config("anthropic"),
        )
        google = resolve_provider_model(
            model_name="gemini-2.5-flash",
            provider=_config("google"),
        )
        self.assertIsInstance(anthropic, AnthropicModel)
        self.assertIsInstance(anthropic._provider, AnthropicProvider)
        self.assertIsInstance(google, GoogleModel)
        self.assertIsInstance(google._provider, GoogleProvider)

    def test_openai_compatible_requires_and_uses_custom_endpoint(self) -> None:
        model = resolve_provider_model(
            model_name="custom-model",
            provider=_config("openai_compatible", use_default_base_url=False),
        )
        self.assertIsInstance(model, OpenAIChatModel)
        self.assertIs(type(model._provider), OpenAIProvider)

    def test_video_capability_selects_video_adapter(self) -> None:
        model = resolve_provider_model(
            model_name="custom-video-model",
            provider=_config("openai_compatible", use_default_base_url=False),
            input_modalities=["text", "video"],
        )
        self.assertIsInstance(model, OpenAICompatibleVideoChatModel)

    def test_empty_model_name_fails_before_request(self) -> None:
        with self.assertRaisesRegex(ProviderConfigurationError, "model name"):
            resolve_provider_model(model_name="  ", provider=_config("deepseek"))


if __name__ == "__main__":
    unittest.main()
