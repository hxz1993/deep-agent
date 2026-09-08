"""Compatibility alias; never construct a second gateway application."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("deep_agent.channels.gateway.app")
