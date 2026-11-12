from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from pydantic_ai import FunctionToolset, ModelRetry, RunContext, ToolFailed
from pydantic_ai.messages import BinaryContent

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_errors import tool_error_message
from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.mcp.toolset_builder import build_resource_client
from deep_agent.db.models import McpServer
from deep_agent.mcp_request_context import resolve_mcp_request_context_mode
from deep_agent.schemas.mcp import McpInspection
from deep_agent.services.mcp_bound_app import bound_mcp_headers, restricted_mcp_http_client

MAX_RESOURCE_TEXT_BYTES = 256 * 1024


def _selected_resource_rows(spec: AgentSpec, rows: list[McpServer]) -> dict[str, McpServer]:
    selected = {
        capability.removeprefix("mcp:")
        for capability in (spec.capabilities or [])
        if capability.startswith("mcp:")
    }
    eligible: dict[str, McpServer] = {}
    for row in rows:
        if row.name not in selected or row.transport != "url":
            continue
        inspection = McpInspection.from_value(
            getattr(row, "inspection", None),
            transport=row.transport,
        )
        if (
            inspection.status in {"healthy", "degraded"}
            and inspection.resources.status == "supported"
        ):
            eligible[row.name] = row
    return eligible


def _resource_summary(item: Any) -> dict[str, Any]:
    uri = getattr(item, "uri", None)
    result = {
        "name": getattr(item, "name", None),
        "title": getattr(item, "title", None),
        "description": getattr(item, "description", None),
        "mime_type": getattr(item, "mimeType", None) or getattr(item, "mime_type", None),
        "uri": str(uri) if uri is not None else None,
        "size": getattr(item, "size", None),
    }
    return {key: value for key, value in result.items() if value is not None}


def _normalize_resource_content(content: Any) -> dict[str, Any]:
    parts = content if isinstance(content, list) else [content]
    normalized: list[dict[str, Any]] = []
    text_bytes = 0
    for part in parts:
        if isinstance(part, str):
            text_bytes += len(part.encode("utf-8"))
            if text_bytes > MAX_RESOURCE_TEXT_BYTES:
                raise ModelRetry(
                    f"Resource text exceeds the {MAX_RESOURCE_TEXT_BYTES}-byte safety limit; "
                    "read a narrower resource URI."
                )
            normalized.append({"type": "text", "text": part})
        elif isinstance(part, BinaryContent):
            normalized.append(
                {
                    "type": "binary",
                    "media_type": str(part.media_type),
                    "size": len(part.data),
                    "message": "Binary resource content is not inserted into the model context.",
                }
            )
        else:
            normalized.append({"type": "unknown", "value": str(part)[:1000]})
    return {"content": normalized}


def build_mcp_resource_toolset(
    spec: AgentSpec,
    rows: list[McpServer],
) -> FunctionToolset[AgentDeps] | None:
    """Expose host-side Resource proxies only for selected, detected MCP servers."""
    eligible = _selected_resource_rows(spec, rows)
    if not eligible:
        return None

    names = ", ".join(sorted(eligible))
    toolset = FunctionToolset[AgentDeps](
        id="mcp-resources",
        instructions=(
            "Use MCP resource tools to inspect application-provided static context. "
            f"Available resource-capable MCP servers: {names}."
        ),
    )

    def _row(server_name: str) -> McpServer:
        row = eligible.get(server_name)
        if row is None:
            raise ModelRetry(
                f"MCP server {server_name!r} is not resource-capable for this Agent. "
                f"Choose one of: {names}."
            )
        return row

    @asynccontextmanager
    async def _client(ctx: RunContext[AgentDeps], server_name: str):
        row = _row(server_name)
        if resolve_mcp_request_context_mode(row.transport, row.config) != "bound_app":
            yield build_resource_client(
                row.name, row.transport, row.config, request_headers=dict(ctx.deps.request_headers),
            )
            return
        connector = ctx.deps.bound_mcp_connectors.get(server_name)
        if connector is not None:
            yield await connector(ctx)
            return
        headers = await bound_mcp_headers(row.config, ctx.deps)
        http_client = restricted_mcp_http_client(row.config["url"], headers)
        try:
            yield build_resource_client(row.name, row.transport, row.config, http_client=http_client)
        finally:
            await http_client.aclose()

    @toolset.tool
    async def list_mcp_resources(ctx: RunContext[AgentDeps], server_name: str) -> dict[str, Any]:
        """List static resources exposed by one resource-capable MCP server."""
        try:
            async with _client(ctx, server_name) as client:
                resources = await client.list_resources()
        except (ModelRetry, ToolFailed):
            raise
        except Exception as exc:
            raise ToolFailed(
                tool_error_message(f"Could not list resources from {server_name!r}", exc, ctx.deps.request_headers)
            ) from exc
        return {
            "server_name": server_name,
            "resources": [_resource_summary(item) for item in resources],
        }

    @toolset.tool
    async def read_mcp_resource(
        ctx: RunContext[AgentDeps],
        server_name: str,
        uri: str,
    ) -> dict[str, Any]:
        """Read a text resource by exact URI from one resource-capable MCP server."""
        try:
            async with _client(ctx, server_name) as client:
                content = await client.read_resource(uri)
            normalized = _normalize_resource_content(content)
        except (ModelRetry, ToolFailed):
            raise
        except Exception as exc:
            raise ToolFailed(
                tool_error_message(f"Could not read resource from {server_name!r}", exc, ctx.deps.request_headers)
            ) from exc
        return {"server_name": server_name, "uri": uri, **normalized}

    return toolset
