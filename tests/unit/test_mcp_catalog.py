from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from deep_agent.api.v1.admin import require_console_admin
from deep_agent.api.v1.mcp import router
from deep_agent.core.context import _hydrate_builtin_mcp_catalogs
from deep_agent.db.deps import get_mcp_store
from deep_agent.schemas.mcp import McpServerCreate
from tests.helpers.admin_auth import allow_console_admin
from tests.helpers.sqlite_stores import make_sqlite_stores


class McpCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, _ = await make_sqlite_stores()
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api/v1")
        allow_console_admin(self.app)
        self.app.dependency_overrides[get_mcp_store] = lambda: self.stores.mcp
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()

    async def create(self, inspection=None):
        return await self.stores.mcp.create_unique(McpServerCreate(
            name="example", transport="url", config={"url": "https://example.invalid/mcp"},
        ), inspection=inspection)

    async def test_inventory_preserves_snapshot_order_without_connecting_or_writing(self):
        inspection = {"status": "healthy", "tools": {
            "status": "supported", "count": 2, "snapshot_status": "ready",
            "snapshot": {"server_instructions": "PRIVATE_INSTRUCTIONS", "definitions": [
                {"name": "z_tool", "description": "first", "input_schema": {"type": "object"},
                 "annotations": {"readOnlyHint": True}},
                {"name": "a_tool", "title": "second", "output_schema": {"type": "string"}},
            ]},
        }}
        await self.create(inspection)
        with patch("deep_agent.api.v1.mcp.inspect_mcp_server", new_callable=AsyncMock) as inspect:
            response = await self.client.get("/api/v1/mcp/example/catalog")
            inspect.assert_not_awaited()
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual([tool["name"] for tool in payload["tools"]["definitions"]], ["z_tool", "a_tool"])
        self.assertEqual(payload["tools"]["definitions"][0]["annotations"], {"readOnlyHint": True})
        self.assertNotIn("PRIVATE_INSTRUCTIONS", response.text)
        self.assertNotIn("config", payload)
        self.assertEqual((await self.stores.mcp.get_by_name("example")).inspection, inspection)
        ordinary = await self.client.get("/api/v1/mcp/example")
        self.assertNotIn("definitions", ordinary.text)

    async def test_missing_stale_and_invalid_snapshots_are_explicit(self):
        await self.create()
        for stored, expected in [
            (None, "missing"),
            ({"status": "stale", "tools": {"snapshot_status": "stale"}}, "stale"),
            ({"tools": {"snapshot_status": "ready"}}, "missing"),
            ({"tools": {"snapshot": {"schema_version": "future"}}}, "missing"),
        ]:
            await self.stores.mcp.update_inspection("example", stored)
            response = await self.client.get("/api/v1/mcp/example/catalog")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["tools"]["snapshot_status"], expected)
            self.assertEqual(response.json()["tools"]["definitions"], [])

    async def test_resource_snapshot_roundtrip_and_public_exclusion(self):
        from deep_agent.schemas.mcp import McpInspection
        from deep_agent.capabilities.mcp.inspection import _resource_definitions
        from mcp.types import Resource
        definitions = _resource_definitions([Resource(name="guide", uri="docs://guide", description="Guide", mimeType="text/plain")])
        inspection = McpInspection.model_validate({"status": "healthy", "resources": {"status": "supported", "count": 1, "definitions": definitions}})
        await self.create(inspection.to_storage_value())
        response = await self.client.get("/api/v1/mcp/example/catalog")
        self.assertEqual(response.json()["resources"]["definitions"][0]["uri"], "docs://guide")
        self.assertEqual(response.json()["resources"]["snapshot_status"], "ready")
        self.assertNotIn("docs://guide", (await self.client.get("/api/v1/mcp/example")).text)

    async def test_not_found_and_admin_gate(self):
        response = await self.client.get("/api/v1/mcp/missing/catalog")
        self.assertEqual(response.status_code, 404)

        def denied():
            raise HTTPException(status_code=403, detail="admin required")

        self.app.dependency_overrides[require_console_admin] = denied
        response = await self.client.get("/api/v1/mcp/missing/catalog")
        self.assertEqual(response.status_code, 403)

    async def test_startup_fills_missing_builtin_snapshot_once(self):
        row = SimpleNamespace(name="deepcube", transport="builtin", config={}, inspection={"status": "not_applicable"})
        stores = SimpleNamespace(mcp=SimpleNamespace(
            list_all=AsyncMock(return_value=[row]), update_inspection=AsyncMock(),
        ))
        from deep_agent.schemas.mcp import McpInspection, McpInspectionResult
        result = McpInspectionResult.model_validate({
            "inspection": {"status": "not_applicable", "tools": {
                "status": "supported", "count": 1, "snapshot_status": "ready",
                "snapshot": {"definitions": [{"name": "query"}]},
            }},
            "changes": {"status": {"before": "not_applicable", "after": "not_applicable"},
                        "tool_count": {"before": 0, "after": 1},
                        "resource_count": {"before": 0, "after": 0},
                        "template_count": {"before": 0, "after": 0}},
        })
        with patch("deep_agent.core.context.inspect_mcp_server", new=AsyncMock(return_value=result)) as inspect:
            await _hydrate_builtin_mcp_catalogs(stores)
            inspect.assert_awaited_once_with(row)
            stores.mcp.update_inspection.assert_awaited_once()
            row.inspection = McpInspection.model_validate(result.inspection).to_storage_value()
            await _hydrate_builtin_mcp_catalogs(stores)
            inspect.assert_awaited_once()
