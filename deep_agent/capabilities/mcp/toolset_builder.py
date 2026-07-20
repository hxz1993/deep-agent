from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets._dynamic import DynamicToolset

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities.mcp.builtin_config import normalize_mcp_config
from deep_agent.capabilities.mcp.request_context import inject_request_meta
from deep_agent.capabilities.mcp.run_scoped import RunScopedMcpToolset
from deep_agent.mcp_request_context import resolve_mcp_headers
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
    if transport == "builtin":
        return RunScopedMcpToolset(server_name=name, config=config).prefixed(name)
    if transport == "url":
        return _build_url_toolset(name, config)
    raise ValueError(f"Unsupported MCP transport: {transport!r}")


def build_discovery_toolset(
    name: str,
    transport: str,
    config: dict[str, Any],
    *,
    request_headers: dict[str, str] | None = None,
) -> Any:
    """Unprefixed MCP toolset for platform discovery calls (ui_tools_list, resources)."""
    transport, config = normalize_mcp_config(name, transport, config)
    if transport != "url":
        raise ValueError(f"UI discovery requires url transport, got {transport!r}")
    url = config["url"]
    headers = resolve_mcp_headers(transport, config, request_headers)
    init_timeout = resolve_mcp_init_timeout(config)
    return MCPToolset(
        url,
        id=f"discovery:{name}",
        headers=headers,
        init_timeout=init_timeout,
        max_retries=resolve_mcp_max_retries(),
    )


def build_raw_url_mcp_toolset(
    name: str,
    url: str,
    *,
    static_headers: dict[str, str] | None = None,
    init_timeout: float | None = None,
) -> MCPToolset[AgentDeps]:
    timeout = resolve_mcp_init_timeout({"init_timeout": init_timeout})
    headers = dict(static_headers or {})
    return MCPToolset(
        url,
        id=name,
        headers=headers or None,
        process_tool_call=inject_request_meta,
        init_timeout=timeout,
        max_retries=resolve_mcp_max_retries(),
    )


def _build_url_toolset(name: str, config: dict[str, Any]) -> DynamicToolset[AgentDeps]:
    url = config["url"]
    init_timeout = resolve_mcp_init_timeout(config)

    def factory(ctx: RunContext[AgentDeps]) -> MCPToolset[AgentDeps]:
        headers = resolve_mcp_headers("url", config, dict(ctx.deps.request_headers) or None)
        return MCPToolset(
            url,
            id=name,
            headers=headers,
            init_timeout=init_timeout,
            max_retries=resolve_mcp_max_retries(),
        ).prefixed(name)

    return DynamicToolset(factory, per_run_step=False, id=f"mcp:{name}")
