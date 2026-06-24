from __future__ import annotations

import os
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets._dynamic import DynamicToolset

from deep_agent import DEEP_AGENT_ROOT
from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities.mcp.builtin_config import normalize_mcp_config
from deep_agent.capabilities.mcp.request_context import (
    apply_request_headers_to_stdio_config,
    inject_request_meta,
    merge_mcp_headers,
)
from deep_agent.capabilities.mcp.serializing import SerializingToolset
from deep_agent.capabilities.mcp.session_scoped import SessionScopedMcpToolset
from deep_agent.options import OPTION


def resolve_mcp_init_timeout(config: dict[str, Any]) -> float:
    """Per-server ``config.init_timeout`` overrides the global default."""
    raw = config.get("init_timeout")
    if raw is not None:
        return float(raw)
    return float(OPTION.agent.mcp_init_timeout)


def resolve_mcp_max_retries() -> int:
    """Tool arg validation retries for MCP tools (matches Agent ``retries``)."""
    return OPTION.agent.request_retries


def build_mcp_toolset(name: str, transport: str, config: dict[str, Any]) -> Any:
    """Build a registry-ready MCP toolset with request header forwarding."""
    transport, config = normalize_mcp_config(name, transport, config)
    if transport == "url":
        return _build_url_toolset(name, config)
    if transport == "stdio":
        return _build_stdio_toolset(name, config)
    raise ValueError(f"Unsupported MCP transport: {transport!r}")


def build_discovery_toolset(name: str, transport: str, config: dict[str, Any]) -> Any:
    """Unprefixed MCP toolset for platform discovery calls (ui_tools_list, resources)."""
    transport, config = normalize_mcp_config(name, transport, config)
    if transport == "url":
        url = config["url"]
        static_headers = dict(config.get("headers") or {})
        init_timeout = resolve_mcp_init_timeout(config)
        return MCPToolset(
            url,
            id=f"discovery:{name}",
            headers=static_headers,
            init_timeout=init_timeout,
            max_retries=resolve_mcp_max_retries(),
        )
    if transport == "stdio":
        return build_raw_stdio_mcp_toolset(name, config)
    raise ValueError(f"Unsupported MCP transport: {transport!r}")


def _expand_python_path(env: dict[str, str], path: str):
    sep = ";" if os.name == "nt" else ":"
    if "PYTHONPATH" in env: 
        env["PYTHONPATH"] = sep.join([env["PYTHONPATH"], path])
    else:
        env["PYTHONPATH"] = path


def build_raw_stdio_mcp_toolset(name: str, config: dict[str, Any]) -> SerializingToolset:
    from fastmcp.client.transports import StdioTransport

    env = os.environ.copy()
    env.update(config.get("env", {}))
    _expand_python_path(env, str(DEEP_AGENT_ROOT))

    transport_obj = StdioTransport(
        command=config["command"],
        args=list(config.get("args", [])),
        env=env,
        cwd=config.get("cwd"),
    )
    return SerializingToolset(
        MCPToolset(
            transport_obj,
            id=name,
            process_tool_call=inject_request_meta,
            init_timeout=resolve_mcp_init_timeout(config),
            max_retries=resolve_mcp_max_retries(),
        )
    )


def _build_url_toolset(name: str, config: dict[str, Any]) -> DynamicToolset[AgentDeps]:
    url = config["url"]
    static_headers = dict(config.get("headers") or {})
    init_timeout = resolve_mcp_init_timeout(config)

    def factory(ctx: RunContext[AgentDeps]) -> MCPToolset[AgentDeps]:
        headers = merge_mcp_headers(static_headers, dict(ctx.deps.request_headers) or None)
        return MCPToolset(
            url,
            id=name,
            headers=headers,
            init_timeout=init_timeout,
            max_retries=resolve_mcp_max_retries(),
        ).prefixed(name)

    return DynamicToolset(factory, per_run_step=False, id=f"mcp:{name}")


def _build_stdio_toolset(name: str, config: dict[str, Any]) -> Any:
    if OPTION.agent.mcp_stdio_session_scope:
        return SessionScopedMcpToolset(server_name=name, config=config).prefixed(name)

    def factory(ctx: RunContext[AgentDeps]) -> MCPToolset[AgentDeps]:
        request_headers = dict(ctx.deps.request_headers) if ctx.deps.request_headers else None
        spawn_config = apply_request_headers_to_stdio_config(config, request_headers)
        return build_raw_stdio_mcp_toolset(name, spawn_config).prefixed(name)

    return DynamicToolset(factory, per_run_step=False, id=f"mcp:{name}")

