"""Small provider profiles for services without a dedicated Pydantic AI provider."""
from pydantic_ai import ModelProfile
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider


class MiniMaxProvider(AnthropicProvider):
    @property
    def name(self) -> str:
        return "minimax"

    @staticmethod
    def model_profile(model_name: str) -> ModelProfile:
        return {
            **(AnthropicProvider.model_profile(model_name) or {}),
            # Anthropic's hosted tools are not MiniMax API capabilities.
            "supported_native_tools": frozenset(),
            "supports_json_schema_output": False,
        }


class StepFunProvider(OpenAIProvider):
    @property
    def name(self) -> str:
        return "stepfun"

    @staticmethod
    def model_profile(model_name: str) -> ModelProfile:
        return {
            **(OpenAIProvider.model_profile(model_name) or {}),
            "openai_chat_supports_max_completion_tokens": False,
            "openai_supports_strict_tool_definition": False,
            "openai_chat_thinking_field": "reasoning_content",
            "openai_chat_send_back_thinking_parts": "field",
        }
