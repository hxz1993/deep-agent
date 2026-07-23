from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from unittest.mock import patch

from mcp.types import Resource, ResourceTemplate
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage
from starlette.requests import Request

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.spec import AgentSpec
from deep_agent.api.v1.mcp import create_mcp_server, probe_mcp_resources
from deep_agent.capabilities.mcp.resources import (
    MAX_RESOURCE_TEXT_BYTES,
    build_mcp_resource_toolset,
    probe_mcp_resource_capabilities,
)
from deep_agent.db.models import McpServer
from deep_agent.schemas.mcp import McpResourceCapabilities, McpServerCreate


class _FakeResourceClient:
    def __init__(
        self,
        *,
        resources: list[Resource] | None = None,
        templates: list[ResourceTemplate] | None = None,
        content: object = "resource text",
        error: Exception | None = None,
    ) -> None:
        self.resources = resources or []
        self.templates = templates or []
        self.content = content
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def list_resources(self):
        if self.error:
            raise self.error
        return self.resources

    async def list_resource_templates(self):
        if self.error:
            raise self.error
        return self.templates

    async def read_resource(self, _uri: str):
        if self.error:
            raise self.error
        return self.content


def _row(
    name: str = "docs",
    *,
    status: str = "supported",
    enabled: bool = True,
    transport: str = "url",
) -> McpServer:
    return McpServer(
        name=name,
        transport=transport,
        config={"url": f"http://{name}.example/mcp"},
        enabled=enabled,
        resource_capabilities={
            "status": status,
            "resource_count": 1 if status == "supported" else 0,
            "template_count": 0,
        },
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


class McpResourceProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_marks_resources_supported(self) -> None:
        client = _FakeResourceClient(
            resources=[
                Resource(
                    name="guide",
                    uri="deepfos://guide",
                    description="Guide",
                    mimeType="text/markdown",
                )
            ]
        )
        with patch(
            "deep_agent.capabilities.mcp.resources.build_resource_client",
            return_value=client,
        ):
            result = await probe_mcp_resource_capabilities(_row(status="unknown"))

        self.assertEqual(result.status, "supported")
        self.assertEqual(result.resource_count, 1)
        self.assertEqual(result.template_count, 0)
        self.assertIsNone(result.error)

    async def test_probe_marks_templates_supported(self) -> None:
        client = _FakeResourceClient(
            templates=[
                ResourceTemplate(
                    name="cube",
                    uriTemplate="deepfos://cube/{name}",
                    mimeType="application/json",
                )
            ]
        )
        with patch(
            "deep_agent.capabilities.mcp.resources.build_resource_client",
            return_value=client,
        ):
            result = await probe_mcp_resource_capabilities(_row(status="unknown"))

        self.assertEqual(result.status, "supported")
        self.assertEqual(result.resource_count, 0)
        self.assertEqual(result.template_count, 1)

    async def test_probe_marks_empty_server_unsupported(self) -> None:
        with patch(
            "deep_agent.capabilities.mcp.resources.build_resource_client",
            return_value=_FakeResourceClient(),
        ):
            result = await probe_mcp_resource_capabilities(_row(status="unknown"))

        self.assertEqual(result.status, "unsupported")
        self.assertIsNotNone(result.checked_at)

    async def test_probe_failure_is_unknown_without_secret_details(self) -> None:
        with patch(
            "deep_agent.capabilities.mcp.resources.build_resource_client",
            return_value=_FakeResourceClient(error=RuntimeError("secret-token")),
        ):
            result = await probe_mcp_resource_capabilities(_row(status="unknown"))

        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.error, "Resource detection failed (RuntimeError)")
        self.assertNotIn("secret-token", result.error or "")


class _FakeMcpStore:
    def __init__(self, row: McpServer | None = None) -> None:
        self.row = row
        self.saved_capabilities: dict | None = None

    async def get_by_name(self, _name: str) -> McpServer | None:
        return self.row

    async def create_unique(self, data: McpServerCreate) -> McpServer:
        self.row = _row(data.name, status="unknown")
        self.row.config = data.config
        self.row.description = data.description
        self.row.enabled = data.enabled
        return self.row

    async def update_resource_capabilities(
        self,
        _name: str,
        capabilities: dict,
    ) -> McpServer | None:
        assert self.row is not None
        self.saved_capabilities = capabilities
        self.row.resource_capabilities = capabilities
        return self.row


def _request(*headers: tuple[bytes, bytes]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/mcp",
            "headers": list(headers),
        }
    )


class McpResourceApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_succeeds_when_probe_records_unknown(self) -> None:
        store = _FakeMcpStore()
        detected = McpResourceCapabilities(
            status="unknown",
            error="Resource detection failed (TimeoutError)",
        )
        with patch(
            "deep_agent.api.v1.mcp.probe_mcp_resource_capabilities",
            new=AsyncMock(return_value=detected),
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

        self.assertEqual(response.resource_capabilities.status, "unknown")
        self.assertEqual(store.saved_capabilities["status"], "unknown")

    async def test_manual_probe_persists_supported_status(self) -> None:
        store = _FakeMcpStore(_row("docs", status="unknown"))
        detected = McpResourceCapabilities(
            status="supported",
            resource_count=2,
            template_count=1,
        )
        with patch(
            "deep_agent.api.v1.mcp.probe_mcp_resource_capabilities",
            new=AsyncMock(return_value=detected),
        ):
            response = await probe_mcp_resources(
                "docs",
                _request((b"app", b"app-a")),
                store,
            )

        self.assertEqual(response.status, "supported")
        self.assertEqual(response.resource_count, 2)
        self.assertEqual(store.saved_capabilities["template_count"], 1)


class McpResourceToolsetTests(unittest.IsolatedAsyncioTestCase):
    async def test_toolset_is_only_exposed_for_selected_supported_mcp(self) -> None:
        supported = _row("docs")
        ordinary = _row("ordinary", status="unsupported")

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
        client = _FakeResourceClient(
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

    async def test_binary_and_oversized_resources_are_not_injected(self) -> None:
        toolset = build_mcp_resource_toolset(_spec("mcp:docs"), [_row("docs")])
        assert toolset is not None

        binary_client = _FakeResourceClient(
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

        large_client = _FakeResourceClient(content="x" * (MAX_RESOURCE_TEXT_BYTES + 1))
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
