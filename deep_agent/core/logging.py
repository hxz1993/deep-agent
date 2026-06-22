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


def configure_logging(level: str | int | None = None) -> None:
    global _CONFIGURED
    if is_configured():
        _CONFIGURED = True
        return
    setup_loguru(_resolve_log_level(level))
    _CONFIGURED = True
