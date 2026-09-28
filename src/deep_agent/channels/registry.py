from __future__ import annotations

from typing import Any, Callable

from .contracts import ChannelAdapter


class AdapterRegistry:
    """Explicit registrations, not a plugin installer or dynamic code loader."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., ChannelAdapter]] = {}
        self._fingerprints: dict[str, Callable[[Any], str]] = {}

    def register(self, channel_type: str, factory, fingerprint) -> None:
        if channel_type in self._factories:
            raise ValueError("Channel adapter is already registered")
        self._factories[channel_type] = factory
        self._fingerprints[channel_type] = fingerprint

    def create(self, account, *, infinite_retry: bool = True) -> ChannelAdapter:
        factory = self._factories.get(account.channel_type)
        if factory is None:
            raise ValueError("Channel adapter is not installed")
        return factory(account, infinite_retry=infinite_retry)

    def message_capabilities(self, channel_type):
        factory = self._factories.get(channel_type)
        provider = getattr(factory, "message_capabilities", None)
        if provider is None:
            raise ValueError("Channel message operations are unavailable")
        return provider()

    def fingerprint(self, account) -> str:
        fingerprint = self._fingerprints.get(account.channel_type)
        if fingerprint is None:
            raise ValueError("Channel adapter is not installed")
        return f"{account.channel_type}:{fingerprint(account)}"


def default_registry() -> AdapterRegistry:
    from .adapters.wecom import WecomAdapter
    from .adapters.feishu import FeishuAdapter
    from .adapters.dingtalk import DingTalkAdapter

    registry = AdapterRegistry()
    registry.register("wecom", WecomAdapter, WecomAdapter.connection_fingerprint)
    registry.register("feishu", FeishuAdapter, FeishuAdapter.connection_fingerprint)
    registry.register("dingtalk", DingTalkAdapter, DingTalkAdapter.connection_fingerprint)
    return registry
