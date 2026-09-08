from __future__ import annotations

from loguru import logger


class SafeSdkLogger:
    """SDK logger that never emits frames, credentials, media URLs or AES keys."""

    def debug(self, _message, *args, **kwargs) -> None:
        return None

    def info(self, message, *args, **kwargs) -> None:
        logger.info("WeCom SDK: {}", self._safe_label(message))

    def warning(self, message, *args, **kwargs) -> None:
        logger.warning("WeCom SDK: {}", self._safe_label(message))

    warn = warning

    def error(self, message, *args, **kwargs) -> None:
        logger.error("WeCom SDK: {}", self._safe_label(message))

    def exception(self, message, *args, **kwargs) -> None:
        logger.error("WeCom SDK: {}", self._safe_label(message))

    @staticmethod
    def _safe_label(message) -> str:
        text = str(message).lower()
        if "auth" in text:
            return "authentication event"
        if "reconnect" in text:
            return "reconnect event"
        if "heartbeat" in text:
            return "heartbeat event"
        if "disconnect" in text or "closed" in text:
            return "connection closed"
        if "connect" in text:
            return "connection event"
        if "timeout" in text:
            return "request timeout"
        return "runtime event"


__all__ = ["SafeSdkLogger"]
