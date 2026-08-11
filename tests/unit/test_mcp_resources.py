from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mcp.types import Resource, ResourceTemplate, Tool, ToolAnnotations, ToolExecution
from pydantic_ai import ModelRetry, RunContext, ToolFailed
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage
from starlette.requests import Request

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.spec import AgentSpec
from deep_agent.api.v1.mcp import (
    create_mcp_server,
    inspect_mcp,
    update_mcp_server,
)
from deep_agent.capabilities.mcp.inspection import inspect_mcp_server
from deep_agent.capabilities.mcp.resources import (
    MAX_RESOURCE_TEXT_BYTES,
    build_mcp_resource_toolset,
)
from deep_agent.db.models import McpServer
from deep_agent.schemas.mcp import (
    McpInspection,
    McpInspectionResult,
    McpServerCreate,
    McpServerUpdate,
)


class _FakeInspectionClient:
    def __init__(
        self,
        *,
        tools: list[Tool] | None = None,
        resources: list[Resource] | None = None,
        templates: list[ResourceTemplate] | None = None,
        has_tools: bool = True,
        has_resources: bool = True,
        ping_result: bool = True,
        errors: dict[str, Exception] | None = None,
        content: object = "resource text",
    ) -> None:
        self.tool_items = tools or []
        self.resource_items = resources or []
        self.template_items = templates or []
        self.capabilities = SimpleNamespace(tools=has_tools, resources=has_resources)
        self.server_info = SimpleNamespace(name="test-mcp", version="1.2.3")
        self.instructions = "Use this MCP carefully."
        self.initialize_result = SimpleNamespace(protocolVersion="2025-06-18")
        self.client = self
        self.ping_result = ping_result
        self.errors = errors or {}
        self.content = content

    async def __aenter__(self):
        if error := self.errors.get("initialize"):
            raise error
        return self

    async def __aexit__(self, *_args):
        return None

    async def ping(self):
        if error := self.errors.get("ping"):
            raise error
        return self.ping_result

    async def list_tools(self):
        if error := self.errors.get("tools"):
            raise error
        return self.tool_items

    async def list_resources(self):
        if error := self.errors.get("resources"):
            raise error
        return self.resource_items

    async def list_resource_templates(self):
        if error := self.errors.get("templates"):
            raise error
        return self.template_items

    async def read_resource(self, _uri: str):
        if error := self.errors.get("read"):
            raise error
        return self.content


def _inspection(
    *,
    status: str = "healthy",
    resource_status: str = "supported",
    tool_count: int = 1,
    resource_count: int = 1,
    template_count: int = 0,
) -> dict:
    definitions = [
        {
            "name": f"tool_{index}",
            "description": "Test tool",
            "input_schema": {"type": "object"},
        }
        for index in range(tool_count)
    ]
    return {
        "status": status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "tools": {
            "status": "supported",
            "count": tool_count,
            "snapshot_status": "ready",
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_hash": "test-snapshot",
            "snapshot": {
                "schema_version": "1.0",
                "server_instructions": "Use test tools.",
                "definitions": definitions,
            },
        },
        "resources": {
            "status": resource_status,
            "count": resource_count,
            "template_count": template_count,
        },
    }


_DEFAULT_INSPECTION = object()


def _row(
    name: str = "docs",
    *,
    inspection: dict | None | object = _DEFAULT_INSPECTION,
    enabled: bool = True,
    transport: str = "url",
) -> McpServer:
    stored_inspection = (
        _inspection()
        if inspection is _DEFAULT_INSPECTION
        else inspection
    )
    return McpServer(
        name=name,
        transport=transport,
        config={"url": f"http://{name}.example/mcp"},
        enabled=enabled,
        inspection=stored_inspection,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _spec(*capabilities: str) -> AgentSpec:
    return AgentSpec(
        name="demo",
        model="test",
        instructions="test",
        capabilities=list(capabilities),
    )


def _ctx(headers: dict[str, str] | None = None) -> RunContext[AgentDeps]:
    return RunContext(
        deps=AgentDeps(session_id="session-1", request_headers=headers or {}),
        model=TestModel(),
        usage=RunUsage(),
    )


def _request(*headers: tuple[bytes, bytes]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/mcp",
            "headers": list(headers),
        }
    )


class McpInspectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_inspection_collects_server_tools_and_resources(self) -> None:
        client = _FakeInspectionClient(
            tools=[
                Tool(
                    name="query",
                    description="Query the catalog.",
                    inputSchema={"type": "object"},
                    outputSchema={"type": "object", "properties": {"rows": {"type": "array"}}},
                    annotations=ToolAnnotations(readOnlyHint=True),
                    execution=ToolExecution(taskSupport="optional"),
                    _meta={"authorization": "secret-token"},
                )
            ],
            resources=[
                Resource(
                    name="guide",
                    uri="deepfos://guide",
                    mimeType="text/markdown",
                )
            ],
            templates=[
                ResourceTemplate(
                    name="cube",
                    uriTemplate="deepfos://cube/{name}",
                )
            ],
        )
        with patch(
            "deep_agent.capabilities.mcp.inspection.build_resource_client",
            return_value=client,
        ) as build_client:
            result = await inspect_mcp_server(
                _row(inspection=None),
                request_headers={"user": "4", "app": "app-a"},
            )

        self.assertEqual(result.inspection.status, "healthy")
        self.assertEqual(result.inspection.server.name, "test-mcp")
        self.assertEqual(result.inspection.server.version, "1.2.3")
        self.assertEqual(result.inspection.server.protocol_version, "2025-06-18")
        self.assertTrue(result.inspection.server.instructions_available)
        self.assertEqual(result.inspection.tools.count, 1)
        self.assertEqual(result.inspection.tools.snapshot_status, "ready")
        self.assertEqual(
            result.inspection.tools.snapshot.definitions[0].name,
            "query",
        )
        definition = result.inspection.tools.snapshot.definitions[0]
        self.assertEqual(definition.output_schema["type"], "object")
        self.assertTrue(definition.annotations["readOnlyHint"])
        self.assertEqual(definition.task_support, "optional")
        self.assertEqual(
            result.inspection.tools.snapshot.server_instructions,
            "Use this MCP carefully.",
        )
        public_value = result.inspection.model_dump(mode="json")
        self.assertNotIn("snapshot", public_value["tools"])
        stored_value = result.inspection.to_storage_value()
        self.assertEqual(
            stored_value["tools"]["snapshot"]["definitions"][0]["name"],
            "query",
        )
        self.assertNotIn("secret-token", json.dumps(stored_value))
        self.assertEqual(result.inspection.resources.count, 1)
        self.assertEqual(result.inspection.resources.template_count, 1)
        self.assertEqual(
            build_client.call_args.kwargs["request_headers"],
            {"user": "4", "app": "app-a"},
        )

    async def test_servers_without_declared_capabilities_are_healthy(self) -> None:
        client = _FakeInspectionClient(has_tools=False, has_resources=False)
        with patch(
            "deep_agent.capabilities.mcp.inspection.build_resource_client",
            return_value=client,
        ):
            result = await inspect_mcp_server(_row(inspection=None))

        self.assertEqual(result.inspection.status, "healthy")
        self.assertEqual(result.inspection.tools.status, "unsupported")
        self.assertEqual(result.inspection.tools.snapshot_status, "ready")
        self.assertEqual(result.inspection.tools.snapshot.definitions, [])
        self.assertEqual(result.inspection.resources.status, "unsupported")

    async def test_empty_resource_capability_does_not_enable_resource_tools(self) -> None:
        client = _FakeInspectionClient(has_resources=True)
        with patch(
            "deep_agent.capabilities.mcp.inspection.build_resource_client",
            return_value=client,
        ):
            result = await inspect_mcp_server(_row(inspection=None))

        self.assertEqual(result.inspection.status, "healthy")
        self.assertEqual(result.inspection.resources.status, "unsupported")

    async def test_resource_template_alone_marks_resources_supported(self) -> None:
        client = _FakeInspectionClient(
            has_resources=True,
            templates=[
                ResourceTemplate(
                    name="cube",
                    uriTemplate="deepfos://cube/{name}",
                )
            ],
        )
        with patch(
            "deep_agent.capabilities.mcp.inspection.build_resource_client",
            return_value=client,
        ):
            result = await inspect_mcp_server(_row(inspection=None))

        self.assertEqual(result.inspection.status, "healthy")
        self.assertEqual(result.inspection.resources.status, "supported")
        self.assertEqual(result.inspection.resources.count, 0)
        self.assertEqual(result.inspection.resources.template_count, 1)

    async def test_tool_list_failure_keeps_resource_result(self) -> None:
        client = _FakeInspectionClient(
            resources=[
                Resource(
                    name="guide",
                    uri="deepfos://guide",
                    mimeType="text/markdown",
                )
            ],
            errors={"tools": RuntimeError("secret-token")},
        )
        with patch(
            "deep_agent.capabilities.mcp.inspection.build_resource_client",
            return_value=client,
        ):
            result = await inspect_mcp_server(_row(inspection=None))

        self.assertEqual(result.inspection.status, "degraded")
        self.assertEqual(result.inspection.tools.status, "error")
        self.assertEqual(result.inspection.tools.error.stage, "tools/list")
        self.assertNotIn("secret-token", result.inspection.tools.error.message)
        self.assertEqual(result.inspection.resources.status, "supported")
        self.assertEqual(result.inspection.resources.count, 1)

    async def test_tool_list_failure_keeps_previous_successful_tool_snapshot(self) -> None:
        client = _FakeInspectionClient(
            errors={"tools": RuntimeError("secret-token")},
        )
        with patch(
            "deep_agent.capabilities.mcp.inspection.build_resource_client",
            return_value=client,
        ):
            result = await inspect_mcp_server(
                _row(inspection=_inspection(tool_count=2))
            )

        self.assertEqual(result.inspection.status, "degraded")
        self.assertEqual(result.inspection.tools.status, "error")
        self.assertEqual(result.inspection.tools.snapshot_status, "ready")
        self.assertEqual(len(result.inspection.tools.snapshot.definitions), 2)
        self.assertEqual(result.inspection.tools.snapshot_hash, "test-snapshot")

    async def test_list_failure_is_degraded_and_preserves_previous_count(self) -> None:
        client = _FakeInspectionClient(
            errors={"resources": RuntimeError("secret-token")},
        )
        row = _row(
            inspection=_inspection(resource_count=3, template_count=2),
        )
        with patch(
            "deep_agent.capabilities.mcp.inspection.build_resource_client",
            return_value=client,
        ):
            result = await inspect_mcp_server(row)

        self.assertEqual(result.inspection.status, "degraded")
        self.assertEqual(result.inspection.resources.status, "error")
        self.assertEqual(result.inspection.resources.count, 3)
        self.assertEqual(result.inspection.resources.error.stage, "resources/list")
        self.assertNotIn(
            "secret-token",
            result.inspection.resources.error.message,
        )

    async def test_initialization_failure_is_unreachable_and_keeps_snapshot(self) -> None:
        client = _FakeInspectionClient(
            errors={"initialize": TimeoutError("secret-token")},
        )
        row = _row(inspection=_inspection(tool_count=7, resource_count=2))
        with patch(
            "deep_agent.capabilities.mcp.inspection.build_resource_client",
            return_value=client,
        ):
            result = await inspect_mcp_server(row)

        self.assertEqual(result.inspection.status, "unreachable")
        self.assertEqual(result.inspection.tools.count, 7)
        self.assertEqual(result.inspection.tools.snapshot_status, "ready")
        self.assertEqual(len(result.inspection.tools.snapshot.definitions), 7)
        self.assertEqual(result.inspection.resources.count, 2)
        self.assertEqual(result.inspection.error.stage, "initialize")
        self.assertNotIn("secret-token", result.inspection.error.message)

    async def test_client_build_failure_is_unreachable_instead_of_escaping(self) -> None:
        with patch(
            "deep_agent.capabilities.mcp.inspection.build_resource_client",
            side_effect=ValueError("invalid url with secret-token"),
        ):
            result = await inspect_mcp_server(_row(inspection=None))

        self.assertEqual(result.inspection.status, "unreachable")
        self.assertEqual(result.inspection.error.stage, "initialize")
        self.assertNotIn("secret-token", result.inspection.error.message)

    async def test_failed_ping_is_unreachable(self) -> None:
        with patch(
            "deep_agent.capabilities.mcp.inspection.build_resource_client",
            return_value=_FakeInspectionClient(ping_result=False),
        ):
            result = await inspect_mcp_server(_row(inspection=None))

        self.assertEqual(result.inspection.status, "unreachable")
        self.assertEqual(result.inspection.error.stage, "ping")

    async def test_builtin_is_not_started(self) -> None:
        row = _row("deepcube", transport="builtin", inspection=None)
        with patch(
            "deep_agent.capabilities.mcp.inspection.build_resource_client"
        ) as build_client:
            result = await inspect_mcp_server(row)

        self.assertEqual(result.inspection.status, "not_applicable")
        build_client.assert_not_called()


class _FakeMcpStore:
    def __init__(self, row: McpServer | None = None) -> None:
        self.row = row
        self.saved_inspection: dict | None = None

    async def get_by_name(self, _name: str) -> McpServer | None:
        return self.row

    async def create_unique(
        self,
        data: McpServerCreate,
        *,
        inspection: dict | None = None,
    ) -> McpServer:
        self.row = _row(data.name, inspection=None, enabled=data.enabled)
        self.row.config = data.config
        self.row.description = data.description
        self.row.inspection = inspection
        self.saved_inspection = inspection
        return self.row

    async def update(
        self,
        _name: str,
        data: McpServerUpdate,
        *,
        inspection: dict | None = None,
    ) -> McpServer | None:
        assert self.row is not None
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(self.row, field, value)
        self.row.inspection = inspection
        self.saved_inspection = inspection
        return self.row

    async def update_inspection(
        self,
        _name: str,
        inspection: dict,
    ) -> McpServer | None:
        assert self.row is not None
        self.saved_inspection = inspection
        self.row.inspection = inspection
        return self.row


class McpInspectionApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_enabled_create_inspects_but_failure_does_not_block_save(self) -> None:
        store = _FakeMcpStore()
        result = McpInspectionResult.model_validate(
            {
                "inspection": {
                    "status": "unreachable",
                    "error": {
                        "stage": "initialize",
                        "type": "TimeoutError",
                        "message": "MCP initialize timed out",
                    },
                },
                "changes": {
                    "status": {"before": "unknown", "after": "unreachable"},
                    "tool_count": {"before": 0, "after": 0},
                    "resource_count": {"before": 0, "after": 0},
                    "template_count": {"before": 0, "after": 0},
                },
            }
        )
        with patch(
            "deep_agent.api.v1.mcp.inspect_mcp_server",
            new=AsyncMock(return_value=result),
        ):
            response = await create_mcp_server(
                McpServerCreate(
                    name="docs",
                    transport="url",
                    config={"url": "http://docs.example/mcp"},
                ),
                _request((b"user", b"4")),
                store,
            )

        self.assertEqual(response.inspection.status, "unreachable")
        self.assertEqual(store.saved_inspection["status"], "unreachable")

    async def test_disabled_create_does_not_connect(self) -> None:
        store = _FakeMcpStore()
        with patch(
            "deep_agent.api.v1.mcp.inspect_mcp_server",
            new=AsyncMock(),
        ) as inspect:
            response = await create_mcp_server(
                McpServerCreate(
                    name="docs",
                    transport="url",
                    config={"url": "http://docs.example/mcp"},
                    enabled=False,
                ),
                _request(),
                store,
            )

        self.assertEqual(response.inspection.status, "unknown")
        inspect.assert_not_awaited()

    async def test_create_does_not_persist_when_inspection_pipeline_crashes(self) -> None:
        store = _FakeMcpStore()
        with patch(
            "deep_agent.api.v1.mcp.inspect_mcp_server",
            new=AsyncMock(side_effect=RuntimeError("unexpected inspection failure")),
        ):
            with self.assertRaises(RuntimeError):
                await create_mcp_server(
                    McpServerCreate(
                        name="docs",
                        transport="url",
                        config={"url": "http://docs.example/mcp"},
                    ),
                    _request(),
                    store,
                )

        self.assertIsNone(store.row)

    async def test_manual_inspection_persists_result_and_changes(self) -> None:
        store = _FakeMcpStore(_row("docs", inspection=None, enabled=False))
        detected = McpInspectionResult.model_validate(
            {
                "inspection": {
                    "status": "healthy",
                    "tools": {
                        "status": "supported",
                        "count": 2,
                        "snapshot_status": "ready",
                        "snapshot_hash": "manual-snapshot",
                        "snapshot": {
                            "schema_version": "1.0",
                            "definitions": [
                                {"name": "first", "input_schema": {"type": "object"}},
                                {"name": "second", "input_schema": {"type": "object"}},
                            ],
                        },
                    },
                    "resources": {
                        "status": "supported",
                        "count": 1,
                        "template_count": 1,
                    },
                },
                "changes": {
                    "status": {"before": "unknown", "after": "healthy"},
                    "tool_count": {"before": 0, "after": 2},
                    "resource_count": {"before": 0, "after": 1},
                    "template_count": {"before": 0, "after": 1},
                },
            }
        )
        with patch(
            "deep_agent.api.v1.mcp.inspect_mcp_server",
            new=AsyncMock(return_value=detected),
        ):
            response = await inspect_mcp(
                "docs",
                _request((b"app", b"mdxath003")),
                store,
            )

        self.assertEqual(response.inspection.status, "healthy")
        self.assertEqual(response.changes.tool_count.after, 2)
        self.assertEqual(store.saved_inspection["resources"]["count"], 1)
        self.assertEqual(
            store.saved_inspection["tools"]["snapshot"]["definitions"][0]["name"],
            "first",
        )
        self.assertNotIn("snapshot", response.inspection.model_dump(mode="json")["tools"])

    async def test_disabled_config_update_marks_snapshot_stale_without_connecting(self) -> None:
        store = _FakeMcpStore(_row("docs", enabled=False))
        with patch(
            "deep_agent.api.v1.mcp.inspect_mcp_server",
            new=AsyncMock(),
        ) as inspect:
            response = await update_mcp_server(
                "docs",
                McpServerUpdate(
                    config={"url": "http://new.example/mcp"},
                ),
                _request(),
                store,
            )

        self.assertEqual(response.inspection.status, "stale")
        self.assertEqual(response.inspection.tools.snapshot_status, "stale")
        self.assertIsNone(response.inspection.tools.snapshot)
        self.assertEqual(store.saved_inspection["status"], "stale")
        self.assertNotIn("snapshot", store.saved_inspection["tools"])
        inspect.assert_not_awaited()

    async def test_enabled_config_update_marks_stale_then_reinspects(self) -> None:
        store = _FakeMcpStore(_row("docs", enabled=True))
        detected = McpInspectionResult.model_validate(
            {
                "inspection": {
                    "status": "healthy",
                    "tools": {"status": "supported", "count": 4},
                    "resources": {"status": "unsupported"},
                },
                "changes": {
                    "status": {"before": "stale", "after": "healthy"},
                    "tool_count": {"before": 1, "after": 4},
                    "resource_count": {"before": 1, "after": 0},
                    "template_count": {"before": 0, "after": 0},
                },
            }
        )

        async def inspect_after_stale(row, **_kwargs):
            self.assertEqual(row.inspection["status"], "stale")
            return detected

        with patch(
            "deep_agent.api.v1.mcp.inspect_mcp_server",
            new=AsyncMock(side_effect=inspect_after_stale),
        ) as inspect:
            response = await update_mcp_server(
                "docs",
                McpServerUpdate(
                    config={"url": "http://new.example/mcp"},
                ),
                _request((b"user", b"4")),
                store,
            )

        self.assertEqual(response.inspection.status, "healthy")
        self.assertEqual(response.inspection.tools.count, 4)
        inspect.assert_awaited_once()

    async def test_update_does_not_persist_when_inspection_pipeline_crashes(self) -> None:
        store = _FakeMcpStore(_row("docs", enabled=True))
        original_config = dict(store.row.config)
        with patch(
            "deep_agent.api.v1.mcp.inspect_mcp_server",
            new=AsyncMock(side_effect=RuntimeError("unexpected inspection failure")),
        ):
            with self.assertRaises(RuntimeError):
                await update_mcp_server(
                    "docs",
                    McpServerUpdate(config={"url": "http://new.example/mcp"}),
                    _request(),
                    store,
                )

        self.assertEqual(store.row.config, original_config)

    async def test_enabling_url_mcp_triggers_inspection(self) -> None:
        store = _FakeMcpStore(_row("docs", inspection=None, enabled=False))
        detected = McpInspectionResult.model_validate(
            {
                "inspection": {
                    "status": "healthy",
                    "tools": {"status": "unsupported"},
                    "resources": {"status": "unsupported"},
                },
                "changes": {
                    "status": {"before": "unknown", "after": "healthy"},
                    "tool_count": {"before": 0, "after": 0},
                    "resource_count": {"before": 0, "after": 0},
                    "template_count": {"before": 0, "after": 0},
                },
            }
        )
        with patch(
            "deep_agent.api.v1.mcp.inspect_mcp_server",
            new=AsyncMock(return_value=detected),
        ) as inspect:
            response = await update_mcp_server(
                "docs",
                McpServerUpdate(enabled=True),
                _request(),
                store,
            )

        self.assertEqual(response.inspection.status, "healthy")
        inspect.assert_awaited_once()


class McpResourceToolsetTests(unittest.IsolatedAsyncioTestCase):
    async def test_toolset_is_only_exposed_for_selected_supported_mcp(self) -> None:
        supported = _row("docs")
        ordinary = _row(
            "ordinary",
            inspection=_inspection(resource_status="unsupported", resource_count=0),
        )

        self.assertIsNone(build_mcp_resource_toolset(_spec(), [supported]))
        self.assertIsNone(
            build_mcp_resource_toolset(_spec("mcp:ordinary"), [supported, ordinary])
        )

        toolset = build_mcp_resource_toolset(_spec("mcp:docs"), [supported, ordinary])
        self.assertIsNotNone(toolset)
        assert toolset is not None
        self.assertEqual(
            set(toolset.tools),
            {
                "list_mcp_resources",
                "list_mcp_resource_templates",
                "read_mcp_resource",
            },
        )

    async def test_stale_or_unreachable_snapshot_hides_resource_tools(self) -> None:
        for status in ("stale", "unreachable"):
            with self.subTest(status=status):
                row = _row("docs", inspection=_inspection(status=status))
                self.assertIsNone(
                    build_mcp_resource_toolset(_spec("mcp:docs"), [row])
                )

    async def test_list_and_read_route_to_exact_server_with_request_headers(self) -> None:
        resource = Resource(
            name="guide",
            uri="deepfos://guide",
            description="Guide",
            mimeType="text/markdown",
            size=12,
        )
        template = ResourceTemplate(
            name="cube",
            uriTemplate="deepfos://cube/{name}",
            description="Cube guide",
            mimeType="application/json",
        )
        client = _FakeInspectionClient(
            resources=[resource],
            templates=[template],
            content="hello",
        )
        toolset = build_mcp_resource_toolset(_spec("mcp:docs"), [_row("docs")])
        assert toolset is not None
        ctx = _ctx({"user": "4", "app": "app-a"})

        with patch(
            "deep_agent.capabilities.mcp.resources.build_resource_client",
            return_value=client,
        ) as build_client:
            listed = await toolset.tools["list_mcp_resources"].function(ctx, "docs")
            templates = await toolset.tools["list_mcp_resource_templates"].function(
                ctx,
                "docs",
            )
            read = await toolset.tools["read_mcp_resource"].function(
                ctx,
                "docs",
                "deepfos://guide",
            )

        self.assertEqual(listed["resources"][0]["uri"], str(resource.uri))
        self.assertEqual(listed["resources"][0]["mime_type"], "text/markdown")
        self.assertEqual(
            templates["resource_templates"][0]["uri_template"],
            "deepfos://cube/{name}",
        )
        self.assertEqual(read["content"], [{"type": "text", "text": "hello"}])
        self.assertEqual(
            build_client.call_args.kwargs["request_headers"],
            {"user": "4", "app": "app-a"},
        )

    async def test_unselected_server_is_rejected_before_connecting(self) -> None:
        toolset = build_mcp_resource_toolset(_spec("mcp:docs"), [_row("docs")])
        assert toolset is not None
        with patch(
            "deep_agent.capabilities.mcp.resources.build_resource_client"
        ) as build_client:
            with self.assertRaises(ModelRetry):
                await toolset.tools["read_mcp_resource"].function(
                    _ctx(),
                    "private",
                    "deepfos://secret",
                )
        build_client.assert_not_called()

    async def test_upstream_resource_failures_are_terminal(self) -> None:
        toolset = build_mcp_resource_toolset(_spec("mcp:docs"), [_row("docs")])
        assert toolset is not None
        failures = (
            ("list_mcp_resources", "resources", ("docs",)),
            ("list_mcp_resource_templates", "templates", ("docs",)),
            ("read_mcp_resource", "read", ("docs", "deepfos://guide")),
        )
        for tool_name, stage, args in failures:
            with self.subTest(tool=tool_name), patch(
                "deep_agent.capabilities.mcp.resources.build_resource_client",
                return_value=_FakeInspectionClient(
                    errors={stage: RuntimeError("offline")}
                ),
            ):
                with self.assertRaises(ToolFailed):
                    await toolset.tools[tool_name].function(_ctx(), *args)

    async def test_binary_and_oversized_resources_are_not_injected(self) -> None:
        toolset = build_mcp_resource_toolset(_spec("mcp:docs"), [_row("docs")])
        assert toolset is not None

        binary_client = _FakeInspectionClient(
            content=BinaryContent(data=b"abc", media_type="application/octet-stream")
        )
        with patch(
            "deep_agent.capabilities.mcp.resources.build_resource_client",
            return_value=binary_client,
        ):
            binary = await toolset.tools["read_mcp_resource"].function(
                _ctx(),
                "docs",
                "deepfos://binary",
            )
        self.assertEqual(binary["content"][0]["type"], "binary")
        self.assertNotIn("data", binary["content"][0])

        large_client = _FakeInspectionClient(
            content="x" * (MAX_RESOURCE_TEXT_BYTES + 1)
        )
        with patch(
            "deep_agent.capabilities.mcp.resources.build_resource_client",
            return_value=large_client,
        ):
            with self.assertRaises(ModelRetry):
                await toolset.tools["read_mcp_resource"].function(
                    _ctx(),
                    "docs",
                    "deepfos://large",
                )


if __name__ == "__main__":
    unittest.main()
