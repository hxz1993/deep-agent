from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException

from deep_agent.api.v1.mcp import delete_mcp_server
from deep_agent.db.reference_integrity import ReferenceConflict
from deep_agent.schemas.agent import AgentCreate, AgentUpdate
from deep_agent.schemas.mcp import McpServerCreate
from tests.helpers.sqlite_stores import make_sqlite_stores


class McpDeleteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, self.factory = await make_sqlite_stores()
        await self.stores.mcp.create_unique(McpServerCreate(
            name="example", transport="url", config={"url": "https://example.invalid/mcp"},
        ))

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_store_rechecks_references_and_keeps_referenced_mcp(self):
        agent = await self.stores.agent.create_unique(AgentCreate(
            name="MCP user", prompt="test", capabilities=["mcp:example"],
        ))
        with self.assertRaises(ReferenceConflict):
            await self.stores.mcp.delete("example")
        self.assertIsNotNone(await self.stores.mcp.get_by_name("example"))
        await self.stores.agent.update_unique(agent.id, AgentUpdate(capabilities=[]))
        self.assertTrue(await self.stores.mcp.delete("example"))
        self.assertFalse(await self.stores.mcp.delete("example"))

    async def test_late_reference_after_api_precheck_returns_409(self):
        await self.stores.agent.create_unique(AgentCreate(
            name="MCP user", prompt="test", capabilities=["mcp:example"],
        ))
        stale_precheck = SimpleNamespace(list_referencing_capability=AsyncMock(return_value=[]))
        with self.assertRaises(HTTPException) as caught:
            await delete_mcp_server("example", store=self.stores.mcp, agent_store=stale_precheck)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIsNotNone(await self.stores.mcp.get_by_name("example"))
