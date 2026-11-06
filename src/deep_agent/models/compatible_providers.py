"""Small provider profiles for services without a dedicated Pydantic AI provider."""
from openai.types.chat import ChatCompletionChunk
from pydantic_ai import ModelProfile
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIStreamedResponse
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RequestUsage

from deep_agent.models.openai_compatible_video import OpenAICompatibleVideoChatModel


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


class _StepFunStreamedResponse(OpenAIStreamedResponse):
    def _map_usage(self, response: ChatCompletionChunk) -> RequestUsage:
        # The SDK replaces usage in continuous mode. Missing usage is not a
        # new zero total (e.g. a trailing finish/content chunk).
        current = response.usage
        if (self._model_settings or {}).get("openai_continuous_usage_stats") and (
            current is None
            or (
                getattr(current, "prompt_tokens", None) is None
                and getattr(current, "completion_tokens", None) is None
            )
        ):
            return self._usage
        return super()._map_usage(response)


class StepFunChatModel(OpenAIChatModel):
    @property
    def _streamed_response_cls(self) -> type[OpenAIStreamedResponse]:
        return _StepFunStreamedResponse


class StepFunVideoChatModel(StepFunChatModel, OpenAICompatibleVideoChatModel):
    """Keep video mapping and StepFun's cumulative streaming usage together."""
