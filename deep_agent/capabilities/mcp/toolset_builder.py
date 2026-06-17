from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets._dynamic import DynamicToolset

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities.mcp.builtin_config import normalize_mcp_config
from deep_agent.capabilities.mcp.request_context import inject_request_meta, merge_mcp_headers
from deep_agent.capabilities.mcp.session_scoped import SessionScopedMcpToolset
from deep_agent.core.config import get_settings


def resolve_mcp_init_timeout(config: dict[str, Any]) -> float:
    """Per-server ``config.init_timeout`` overrides the global default."""
    raw = config.get("init_timeout")
    if raw is not None:
        return float(raw)
    return float(get_settings().mcp_init_timeout)


def build_mcp_toolset(name: str, transport: str, config: dict[str, Any]) -> Any:
    """Build a registry-ready MCP toolset with request header forwarding."""
    transport, config = normalize_mcp_config(name, transport, config)
    if transport == "url":
        return _build_url_toolset(name, config)
    if transport == "stdio":
        return _build_stdio_toolset(name, config)
    raise ValueError(f"Unsupported MCP transport: {transport!r}")


def build_raw_stdio_mcp_toolset(name: str, config: dict[str, Any]) -> MCPToolset[AgentDeps]:
    from fastmcp.client.transports import StdioTransport

    transport_obj = StdioTransport(
        command=config["command"],
        args=list(config.get("args", [])),
        env=config.get("env"),
        cwd=config.get("cwd"),
    )
    return MCPToolset(
        transport_obj,
        id=name,
        process_tool_call=inject_request_meta,
        init_timeout=resolve_mcp_init_timeout(config),
    )


def _build_url_toolset(name: str, config: dict[str, Any]) -> DynamicToolset[AgentDeps]:
    url = config["url"]
    static_headers = dict(config.get("headers") or {})
    init_timeout = resolve_mcp_init_timeout(config)

    def factory(ctx: RunContext[AgentDeps]) -> MCPToolset[AgentDeps]:
        headers = merge_mcp_headers(static_headers, dict(ctx.deps.request_headers) or None)
        return MCPToolset(url, id=name, headers=headers, init_timeout=init_timeout).prefixed(name)

    return DynamicToolset(factory, per_run_step=False, id=f"mcp:{name}")


def _build_stdio_toolset(name: str, config: dict[str, Any]) -> Any:
    if get_settings().mcp_stdio_session_scope:
        return SessionScopedMcpToolset(server_name=name, config=config).prefixed(name)
    return build_raw_stdio_mcp_toolset(name, config).prefixed(name)
