from __future__ import annotations

import logging

from deepfos_platform.log import InterceptHandler, setup as setup_loguru
from deepfos_platform.options import OPTION as PLATFORM_OPTION

_LOG_ENV_PREFIX = "DEEP_AGENT_"
_CONFIGURED = False


def _resolve_log_level(level: str | int | None) -> str:
    PLATFORM_OPTION.log.set_env_prefix(_LOG_ENV_PREFIX)
    if level is None:
        return str(PLATFORM_OPTION.log.level).upper()
    if isinstance(level, int):
        return logging.getLevelName(level)
    return str(level).upper()


def is_configured() -> bool:
    if _CONFIGURED:
        return True
    return any(isinstance(handler, InterceptHandler) for handler in logging.root.handlers)


class WebhookAccessLogFilter(logging.Filter):
    """Uvicorn keeps its original scope even when an ASGI wrapper copies it."""
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            target = args[2]
            if isinstance(target, str) and "/api/v1/webhooks/" in target.split("?", 1)[0]:
                record.args = (*args[:2], target.split("?", 1)[0], *args[3:])
        return True


def configure_logging(level: str | int | None = None) -> None:
    global _CONFIGURED
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, WebhookAccessLogFilter) for item in access_logger.filters):
        access_logger.addFilter(WebhookAccessLogFilter())
    if is_configured():
        _CONFIGURED = True
        return
    setup_loguru(_resolve_log_level(level))
    _CONFIGURED = True
