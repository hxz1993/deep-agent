from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from loguru import logger

from deep_agent.capabilities.mcp.toolset_builder import (
    build_raw_url_mcp_toolset,
    build_resource_client,
    resolve_mcp_init_timeout,
)
from deep_agent.db.models import McpServer
from deep_agent.schemas.mcp import (
    McpInspection,
    McpInspectionChanges,
    McpInspectionError,
    McpInspectionResult,
    McpInspectionServer,
    McpResourcesInspection,
    McpToolSnapshot,
    McpToolSnapshotDefinition,
    McpToolsInspection,
    McpValueChange,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _safe_error(stage: str, exc: Exception) -> McpInspectionError:
    error_type = type(exc).__name__
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        message = f"MCP {stage} timed out"
    else:
        message = f"MCP {stage} failed ({error_type})"
    return McpInspectionError(stage=stage, type=error_type, message=message)


def _previous_success_at(previous: McpInspection) -> datetime | None:
    if previous.status in {"healthy", "degraded"}:
        return previous.checked_at
    return previous.previous_success_at


def _tool_snapshot(tool_items: list[Any], *, instructions: str | None) -> tuple[McpToolSnapshot, str]:
    definitions: list[McpToolSnapshotDefinition] = []
    for tool in tool_items:
        task_support = tool.execution.taskSupport if tool.execution else None
        definitions.append(
            McpToolSnapshotDefinition(
                name=tool.name,
                title=getattr(tool, "title", None),
                description=tool.description,
                input_schema=tool.inputSchema,
                output_schema=tool.outputSchema,
                annotations=(tool.annotations.model_dump(mode="json") if tool.annotations else None),
                task_support=task_support,
            )
        )
    snapshot = McpToolSnapshot(
        server_instructions=instructions or None,
        definitions=definitions,
    )
    canonical = json.dumps(
        snapshot.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return snapshot, hashlib.sha256(canonical).hexdigest()


def mark_mcp_inspection_stale(row: McpServer) -> McpInspection:
    previous = McpInspection.from_value(
        getattr(row, "inspection", None),
        transport=row.transport,
    )
    return previous.model_copy(
        update={
            "status": "stale",
            "previous_success_at": _previous_success_at(previous),
            "tools": previous.tools.model_copy(
                update={
                    "snapshot_status": "stale",
                    "snapshot_at": None,
                    "snapshot_hash": None,
                    "snapshot": None,
                }
            ),
            "error": None,
        }
    )


def unknown_mcp_inspection() -> McpInspection:
    return McpInspection(status="unknown")


def _changes(before: McpInspection, after: McpInspection) -> McpInspectionChanges:
    return McpInspectionChanges(
        status=McpValueChange(before=before.status, after=after.status),
        tool_count=McpValueChange(before=before.tools.count, after=after.tools.count),
        resource_count=McpValueChange(
            before=before.resources.count,
            after=after.resources.count,
        ),
        template_count=McpValueChange(
            before=before.resources.template_count,
            after=after.resources.template_count,
        ),
    )


async def _inspect_builtin_mcp_server(
    row: McpServer,
    *,
    previous: McpInspection,
    request_headers: dict[str, str] | None,
) -> McpInspectionResult:
    """Refresh metadata and the raw Tool snapshot from a builtin MCP runtime."""
    from deep_agent.capabilities.mcp.lease_manager import get_mcp_lease_manager
    from deep_agent.mcp_request_context import resolve_mcp_headers

    started = perf_counter()
    config = dict(row.config or {})
    headers = resolve_mcp_headers("builtin", config, request_headers) or {}

    def factory(connect_url: str):
        return build_raw_url_mcp_toolset(
            row.name,
            connect_url,
            static_headers=headers,
            init_timeout=config.get("init_timeout"),
        )

    try:
        async with get_mcp_lease_manager().lease(
            row.name,
            f"console-refresh:{uuid4().hex}",
            factory,
            spawn_config=config,
        ) as client:
            inspected_at = _utcnow()
            server_info = client.server_info
            init_result = client.client.initialize_result
            server = McpInspectionServer(
                name=getattr(server_info, "name", None),
                version=getattr(server_info, "version", None),
                protocol_version=str(getattr(init_result, "protocolVersion", "") or "") or None,
                instructions_available=bool(client.instructions),
            )

            empty_snapshot, empty_snapshot_hash = _tool_snapshot(
                [],
                instructions=client.instructions,
            )
            tools = McpToolsInspection(
                status="unsupported",
                snapshot_status="ready",
                snapshot_at=inspected_at,
                snapshot_hash=empty_snapshot_hash,
                snapshot=empty_snapshot,
            )
            resources = McpResourcesInspection(status="unsupported")
            if client.capabilities.tools:
                try:
                    tool_items = await client.list_tools()
                    snapshot, snapshot_hash = _tool_snapshot(
                        tool_items or [],
                        instructions=client.instructions,
                    )
                    tools = McpToolsInspection(
                        status="supported",
                        count=len(tool_items or []),
                        snapshot_status="ready",
                        snapshot_at=inspected_at,
                        snapshot_hash=snapshot_hash,
                        snapshot=snapshot,
                    )
                except Exception as exc:
                    tools = previous.tools.model_copy(
                        update={"status": "error", "error": _safe_error("tools/list", exc)}
                    )

            if client.capabilities.resources:
                resource_items: list[Any] | None = None
                template_items: list[Any] | None = None
                resource_error: McpInspectionError | None = None
                template_error: McpInspectionError | None = None
                try:
                    resource_items = await client.list_resources()
                except Exception as exc:
                    resource_error = _safe_error("resources/list", exc)
                try:
                    template_items = await client.list_resource_templates()
                except Exception as exc:
                    template_error = _safe_error("resources/templates/list", exc)
                resources = McpResourcesInspection(
                    status="error" if resource_error or template_error else (
                        "supported" if resource_items or template_items else "unsupported"
                    ),
                    count=(
                        len(resource_items)
                        if resource_items is not None
                        else previous.resources.count
                    ),
                    template_count=(
                        len(template_items)
                        if template_items is not None
                        else previous.resources.template_count
                    ),
                    error=resource_error or template_error,
                )

            current = McpInspection(
                status="not_applicable",
                checked_at=inspected_at,
                duration_ms=_duration_ms(started),
                server=server,
                tools=tools,
                resources=resources,
            )
    except Exception as exc:
        logger.warning(
            "Builtin MCP catalog refresh failed for {!r} ({})",
            row.name,
            type(exc).__name__,
        )
        current = previous.model_copy(
            update={
                "status": "not_applicable",
                "checked_at": _utcnow(),
                "duration_ms": _duration_ms(started),
                "error": _safe_error("initialize", exc),
            }
        )

    return McpInspectionResult(
        inspection=current,
        changes=_changes(previous, current),
    )


async def inspect_mcp_server(
    row: McpServer,
    *,
    request_headers: dict[str, str] | None = None,
) -> McpInspectionResult:
    """Inspect one URL MCP and retain the previous useful snapshot on hard failures."""
    previous = McpInspection.from_value(
        getattr(row, "inspection", None),
        transport=row.transport,
    )
    if row.transport == "builtin":
        return await _inspect_builtin_mcp_server(
            row,
            previous=previous,
            request_headers=request_headers,
        )
    if row.transport != "url":
        current = McpInspection(status="not_applicable")
        return McpInspectionResult(
            inspection=current,
            changes=_changes(previous, current),
        )

    started = perf_counter()
    try:
        client = build_resource_client(
            row.name,
            row.transport,
            row.config,
            request_headers=request_headers,
        )
        timeout = max(15.0, resolve_mcp_init_timeout(row.config) + 10.0)
    except Exception as exc:
        logger.warning(
            "MCP inspection client build failed for {!r} ({})",
            row.name,
            type(exc).__name__,
        )
        current = McpInspection(
            status="unreachable",
            checked_at=_utcnow(),
            duration_ms=_duration_ms(started),
            previous_success_at=_previous_success_at(previous),
            server=previous.server,
            tools=previous.tools,
            resources=previous.resources,
            error=_safe_error("initialize", exc),
        )
        return McpInspectionResult(
            inspection=current,
            changes=_changes(previous, current),
        )

    async def _inspect() -> McpInspection:
        try:
            async with client:
                server_info = client.server_info
                init_result = client.client.initialize_result
                server = McpInspectionServer(
                    name=getattr(server_info, "name", None),
                    version=getattr(server_info, "version", None),
                    protocol_version=str(getattr(init_result, "protocolVersion", "") or "") or None,
                    instructions_available=bool(client.instructions),
                )

                try:
                    if not await client.client.ping():
                        raise RuntimeError("MCP ping returned an unexpected response")
                except Exception as exc:
                    error = _safe_error("ping", exc)
                    return McpInspection(
                        status="unreachable",
                        checked_at=_utcnow(),
                        duration_ms=_duration_ms(started),
                        previous_success_at=_previous_success_at(previous),
                        server=server,
                        tools=previous.tools,
                        resources=previous.resources,
                        error=error,
                    )

                inspected_at = _utcnow()
                empty_snapshot, empty_snapshot_hash = _tool_snapshot(
                    [],
                    instructions=client.instructions,
                )
                tools = McpToolsInspection(
                    status="unsupported",
                    snapshot_status="ready",
                    snapshot_at=inspected_at,
                    snapshot_hash=empty_snapshot_hash,
                    snapshot=empty_snapshot,
                )
                resources = McpResourcesInspection(status="unsupported")
                degraded = False

                if client.capabilities.tools:
                    try:
                        tool_items = await client.list_tools()
                        snapshot, snapshot_hash = _tool_snapshot(
                            tool_items,
                            instructions=client.instructions,
                        )
                        tools = McpToolsInspection(
                            status="supported",
                            count=len(tool_items),
                            snapshot_status="ready",
                            snapshot_at=inspected_at,
                            snapshot_hash=snapshot_hash,
                            snapshot=snapshot,
                        )
                    except Exception as exc:
                        degraded = True
                        tools = previous.tools.model_copy(
                            update={
                                "status": "error",
                                "error": _safe_error("tools/list", exc),
                            }
                        )

                if client.capabilities.resources:
                    resource_items: list[Any] | None = None
                    template_items: list[Any] | None = None
                    resource_error: McpInspectionError | None = None
                    template_error: McpInspectionError | None = None
                    try:
                        resource_items = await client.list_resources()
                    except Exception as exc:
                        resource_error = _safe_error("resources/list", exc)
                    try:
                        template_items = await client.list_resource_templates()
                    except Exception as exc:
                        template_error = _safe_error("resources/templates/list", exc)

                    if resource_error or template_error:
                        degraded = True
                        resources = McpResourcesInspection(
                            status="error",
                            count=(
                                len(resource_items)
                                if resource_items is not None
                                else previous.resources.count
                            ),
                            template_count=(
                                len(template_items)
                                if template_items is not None
                                else previous.resources.template_count
                            ),
                            error=resource_error or template_error,
                        )
                    else:
                        resource_count = len(resource_items or [])
                        template_count = len(template_items or [])
                        resources = McpResourcesInspection(
                            status=(
                                "supported"
                                if resource_count or template_count
                                else "unsupported"
                            ),
                            count=resource_count,
                            template_count=template_count,
                        )

                return McpInspection(
                    status="degraded" if degraded else "healthy",
                    checked_at=inspected_at,
                    duration_ms=_duration_ms(started),
                    previous_success_at=_previous_success_at(previous),
                    server=server,
                    tools=tools,
                    resources=resources,
                    error=None,
                )
        except Exception as exc:
            logger.warning(
                "MCP inspection failed for {!r} ({})",
                row.name,
                type(exc).__name__,
            )
            error = _safe_error("initialize", exc)
            return McpInspection(
                status="unreachable",
                checked_at=_utcnow(),
                duration_ms=_duration_ms(started),
                previous_success_at=_previous_success_at(previous),
                server=previous.server,
                tools=previous.tools,
                resources=previous.resources,
                error=error,
            )

    try:
        current = await asyncio.wait_for(_inspect(), timeout=timeout)
    except TimeoutError as exc:
        current = McpInspection(
            status="unreachable",
            checked_at=_utcnow(),
            duration_ms=_duration_ms(started),
            previous_success_at=_previous_success_at(previous),
            server=previous.server,
            tools=previous.tools,
            resources=previous.resources,
            error=_safe_error("inspection", exc),
        )

    return McpInspectionResult(
        inspection=current,
        changes=_changes(previous, current),
    )
