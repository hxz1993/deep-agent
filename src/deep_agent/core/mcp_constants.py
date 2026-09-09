"""Shared constants for deep-agent."""

from __future__ import annotations

# JSON env var read by builtin MCP servers at subprocess startup (legacy).
REQUEST_HEADERS_ENV = "DEEP_AGENT_MCP_REQUEST_HEADERS"
# Shared-HTTP builtin coordination (registry json mtime + in-process TTL watcher).
MCP_REGISTRY_JSON_ENV = "DEEP_AGENT_MCP_REGISTRY_JSON"
MCP_SERVER_TTL_ENV = "DEEP_AGENT_MCP_SERVER_TTL"
