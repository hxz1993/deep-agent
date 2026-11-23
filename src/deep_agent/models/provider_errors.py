from pydantic_ai.exceptions import ModelHTTPError, ModelQuotaExceededError

PROVIDER_QUOTA_CODE = "provider_quota_exceeded"
PROVIDER_QUOTA_MESSAGE = "模型供应商额度不足。请补充额度或切换模型后重试。"


def is_provider_quota_error(exc: Exception) -> bool:
    return isinstance(exc, ModelQuotaExceededError) or (
        isinstance(exc, ModelHTTPError) and exc.status_code == 402
    )
