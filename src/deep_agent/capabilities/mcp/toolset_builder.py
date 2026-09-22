from __future__ import annotations

from typing import Any
from urllib.parse import quote

from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import FunctionToolset

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities.mcp.builtin_config import normalize_mcp_config
from deep_agent.capabilities.mcp.request_context import inject_request_meta
from deep_agent.capabilities.mcp.run_scoped import RunScopedMcpToolset
from deep_agent.capabilities.mcp.snapshot_toolset import SnapshotMcpToolset
from deep_agent.mcp_request_context import resolve_mcp_headers
from deep_agent.options import OPTION
from deep_agent.schemas.mcp import McpInspection


def resolve_mcp_init_timeout(config: dict[str, Any]) -> float:
    """Per-server ``config.init_timeout`` overrides the global default."""
    raw = config.get("init_timeout")
    if raw is not None:
        return float(raw)
    return float(OPTION.agent.mcp_init_timeout)


def resolve_mcp_max_retries() -> int:
    """Tool arg validation retries for MCP tools (matches Agent ``retries``)."""
    return OPTION.agent.request_retries


def build_mcp_toolset(
    name: str,
    transport: str,
    config: dict[str, Any],
    *,
    inspection: dict[str, Any] | None = None,
) -> Any:
    """Build a registry-ready MCP toolset with request header forwarding."""
    transport, config = normalize_mcp_config(name, transport, config)
    if transport == "builtin":
        return RunScopedMcpToolset(server_name=name, config=config).prefixed(name)
    if transport == "url":
        parsed = McpInspection.from_value(inspection, transport=transport)
        if parsed.tools.snapshot_status != "ready" or parsed.tools.snapshot is None:
            return FunctionToolset(id=f"mcp-unavailable-{quote(name, safe='')}")
        return SnapshotMcpToolset(
            server_name=name,
            config=config,
            snapshot=parsed.tools.snapshot,
            init_timeout=resolve_mcp_init_timeout(config),
            max_retries=resolve_mcp_max_retries(),
        ).prefixed(name)
    raise ValueError(f"Unsupported MCP transport: {transport!r}")


def build_resource_client(
    name: str,
    transport: str,
    config: dict[str, Any],
    *,
    request_headers: dict[str, str] | None = None,
) -> Any:
    """Unprefixed MCP client for host-side Resource detection and proxy calls."""
    transport, config = normalize_mcp_config(name, transport, config)
    if transport != "url":
        raise ValueError(f"Resource access requires url transport, got {transport!r}")
    url = config["url"]
    headers = resolve_mcp_headers(transport, config, request_headers)
    init_timeout = resolve_mcp_init_timeout(config)
    return MCPToolset(
        url,
        id=f"resources-{quote(name, safe='')}",
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
        id=quote(name, safe=""),
        headers=headers or None,
        include_instructions=True,
        process_tool_call=inject_request_meta,
        init_timeout=timeout,
        max_retries=resolve_mcp_max_retries(),
    )
