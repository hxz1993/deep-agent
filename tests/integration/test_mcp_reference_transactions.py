"""MCP reference races; server database tests require isolated runtime_fk_test."""
from __future__ import annotations

import asyncio
import os
import unittest

from sqlalchemy.engine import make_url

from deep_agent.db.models import McpServer
from deep_agent.db.reference_integrity import acquire_write_barrier, lock_reference
from deep_agent.schemas.agent import AgentUpdate
from deep_agent.schemas.mcp import McpServerCreate
from tests.integration.test_logical_references import ReferenceBehavior


class McpReferenceBehavior:
    url = "sqlite+aiosqlite:///:memory:"
    model_data = ReferenceBehavior.model_data
    agent = ReferenceBehavior.agent
    asyncTearDown = ReferenceBehavior.asyncTearDown

    async def asyncSetUp(self):
        parsed = make_url(self.url)
        if parsed.get_backend_name() != "sqlite" and (
            parsed.database != "runtime_fk_test" or parsed.host not in {"127.0.0.1", "localhost", "::1"}
        ):
            raise RuntimeError("Refusing destructive tests outside local runtime_fk_test")
        await ReferenceBehavior.asyncSetUp(self)
        await self.stores.mcp.create_unique(McpServerCreate(
            name="race", transport="url", config={"url": "https://example.invalid/mcp"},
        ))

    async def assert_no_missing_mcp(self):
        mcp = await self.stores.mcp.get_by_name("race")
        agents = await self.stores.agent.list_all()
        self.assertFalse(mcp is None and any("mcp:race" in a.capabilities for a in agents))

    async def test_agent_create_races_mcp_delete(self):
        results = await asyncio.gather(
            self.agent(capabilities=["mcp:race"]), self.stores.mcp.delete("race"),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1, results)
        self.assertFalse(any(isinstance(result, BaseException) and not isinstance(result, ValueError) for result in results), results)
        await self.assert_no_missing_mcp()

    async def test_agent_update_races_mcp_delete(self):
        agent = await self.agent()
        results = await asyncio.gather(
            self.stores.agent.update_unique(agent.id, AgentUpdate(capabilities=["mcp:race"])),
            self.stores.mcp.delete("race"), return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1, results)
        self.assertFalse(any(isinstance(result, BaseException) and not isinstance(result, ValueError) for result in results), results)
        await self.assert_no_missing_mcp()

    async def test_mcp_parent_lock_blocks_new_reference(self):
        if self.engine.dialect.name == "sqlite":
            self.skipTest("Row-lock behavior requires opt-in MySQL/PostgreSQL")
        async with self.factory() as db:
            async with db.begin():
                await acquire_write_barrier(db)
                await lock_reference(db, McpServer, McpServer.name, "race", "MCP")
                task = asyncio.create_task(self.agent(capabilities=["mcp:race"]))
                try:
                    await asyncio.sleep(.15)
                    self.assertFalse(task.done(), "Agent creation must wait for MCP parent lock")
                finally:
                    await db.commit()
                    await asyncio.wait_for(task, 5)
        await self.assert_no_missing_mcp()


class SQLiteMcpReferences(McpReferenceBehavior, unittest.IsolatedAsyncioTestCase):
    pass


@unittest.skipUnless(os.environ.get("DEEPAGENT_FK_MYSQL_URL"), "isolated MySQL URL not provided")
class MySQLMcpReferences(McpReferenceBehavior, unittest.IsolatedAsyncioTestCase):
    url = os.environ.get("DEEPAGENT_FK_MYSQL_URL", "")


@unittest.skipUnless(os.environ.get("DEEPAGENT_FK_POSTGRES_URL"), "isolated PostgreSQL URL not provided")
class PostgresMcpReferences(McpReferenceBehavior, unittest.IsolatedAsyncioTestCase):
    url = os.environ.get("DEEPAGENT_FK_POSTGRES_URL", "")
