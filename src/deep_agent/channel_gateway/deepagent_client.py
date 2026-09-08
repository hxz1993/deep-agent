"""Compatibility alias for the relocated gateway module."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("deep_agent.channels.gateway.deepagent_client")
