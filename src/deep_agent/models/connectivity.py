"""Transient, bounded checks of saved model/provider configuration, without a Run."""
from __future__ import annotations

import asyncio
import time

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.providers.google import GoogleProvider

from deep_agent.models.provider_registry import ProviderRuntimeConfig, resolve_provider_model
from deep_agent.schemas.llm_model import LlmModelTestResponse

TEST_TIMEOUT_SECONDS = 30


def safe_failure(exc: Exception) -> tuple[str, str]:
    """Never expose exception text, HTTP bodies, credentials, or generated output."""
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__.lower()
    if isinstance(exc, TimeoutError) or "timeout" in name:
        return "timeout", "请求超时，请检查网络或稍后重试"
    if status == 401:
        return "authentication", "认证失败（401），请检查 Provider 的 API Key"
    if status == 403:
        return "permission", "访问被拒绝（403），请检查模型权限"
    if status == 404:
        return "not_found", "模型或接口不存在（404），请检查模型编码和地址"
    if status == 429:
        return "rate_limit", "请求受限（429），请检查额度或稍后重试"
    if isinstance(status, int) and 500 <= status <= 599:
        return "provider_error", f"Provider 服务异常（{status}）"
    if isinstance(exc, ModelHTTPError):
        return "request_rejected", "Provider 拒绝请求，请检查模型与接口配置"
    if "connect" in name or "network" in name:
        return "connection", "连接失败，请检查 Provider 地址和网络"
    if isinstance(exc, UnexpectedModelBehavior):
        return "invalid_response", "模型响应为空或格式不符合协议"
    return "request_failed", "模型请求失败，请检查 Provider 与模型配置"


async def probe_model(row, provider) -> LlmModelTestResponse:
    started = time.monotonic()
    try:
        async with asyncio.timeout(TEST_TIMEOUT_SECONDS):
            model = resolve_provider_model(
                model_name=row.model,
                provider=ProviderRuntimeConfig.from_row(provider),
                input_modalities=row.input_modalities,
                tags=row.tags,
                http_retry_attempts=1,
            )
            settings = {"max_tokens": min(row.max_output_tokens or 64, 64), "timeout": TEST_TIMEOUT_SECONDS}
            # This model/client belongs only to the probe; normal Agent clients are unchanged.
            client = getattr(model.provider, "client", None)
            if isinstance(client, (AsyncOpenAI, AsyncAnthropic)):
                client.max_retries = 0
            try:
                async with model:
                    response = await model.request(
                        [ModelRequest(parts=[UserPromptPart("Reply with OK.")])],
                        settings,
                        ModelRequestParameters(),
                    )
                    if not response.parts or response.finish_reason in {"error", "content_filter"}:
                        raise UnexpectedModelBehavior("Unusable probe response")
            finally:
                if isinstance(model.provider, GoogleProvider):
                    # Also close the probe-owned explicit proxy client, if configured.
                    await model.provider.client.aio.aclose()
        return LlmModelTestResponse(ok=True, message="连接成功", elapsed_ms=int((time.monotonic() - started) * 1000))
    except Exception as exc:
        code, message = safe_failure(exc)
        return LlmModelTestResponse(ok=False, error_code=code, message=message, elapsed_ms=int((time.monotonic() - started) * 1000))
