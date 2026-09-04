"""Compatibility imports; WeCom conversion now belongs to its adapter."""

from deep_agent.channels.adapters.wecom.messages import parse_message
from deep_agent.channels.contracts import InboundMedia, InboundMessage

__all__ = ["InboundMedia", "InboundMessage", "parse_message"]
