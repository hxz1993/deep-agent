"""Compatibility import; SDK logging belongs to the WeCom adapter."""

from deep_agent.channels.adapters.wecom.sdk_logger import SafeSdkLogger

__all__ = ["SafeSdkLogger"]
