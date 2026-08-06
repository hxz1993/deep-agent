from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.schemas.mcp import McpServerCreate
from deep_agent.schemas.agent import AgentCreate
from tests.helpers.sqlite_stores import make_sqlite_stores


class McpConfigurationAtomicityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stores, self._engine, session_factory = await make_sqlite_stores()
        self._app = FastAPI()
        self._app.include_router(api_router)
        init_context(
            AppContext.for_testing(
                registry=build_registry(),
                stores=self.stores,
                mcp_lease_manager=McpLeaseManager(),
                db_engine=self._engine,
                db_session_factory=session_factory,
            )
        )
        self._transport = ASGITransport(app=self._app, raise_app_exceptions=False)

    async def asyncTearDown(self) -> None:
        await get_context().event_persister.aclose()
        reset_context()
        await self._engine.dispose()

    async def test_blank_url_returns_422_without_creating_row(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/mcp",
                json={
                    "name": "blank-url",
                    "transport": "url",
                    "config": {"url": ""},
                    "enabled": True,
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertIsNone(await self.stores.mcp.get_by_name("blank-url"))

    async def test_create_pipeline_failure_does_not_leave_duplicate_row(self) -> None:
        with patch(
            "deep_agent.api.v1.mcp.inspect_mcp_server",
            new=AsyncMock(side_effect=RuntimeError("unexpected inspection failure")),
        ):
            async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/mcp",
                    json={
                        "name": "atomic-create",
                        "transport": "url",
                        "config": {"url": "http://example.test/mcp"},
                        "enabled": True,
                    },
                )

        self.assertEqual(response.status_code, 500)
        self.assertIsNone(await self.stores.mcp.get_by_name("atomic-create"))

    async def test_update_pipeline_failure_preserves_previous_config(self) -> None:
        await self.stores.mcp.create_unique(
            McpServerCreate(
                name="atomic-update",
                transport="url",
                config={"url": "http://old.example.test/mcp"},
                enabled=True,
            ),
            inspection={"status": "healthy"},
        )

        with patch(
            "deep_agent.api.v1.mcp.inspect_mcp_server",
            new=AsyncMock(side_effect=RuntimeError("unexpected inspection failure")),
        ):
            async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                response = await client.patch(
                    "/api/v1/mcp/atomic-update",
                    json={"config": {"url": "http://new.example.test/mcp"}},
                )

        self.assertEqual(response.status_code, 500)
        row = await self.stores.mcp.get_by_name("atomic-update")
        self.assertIsNotNone(row)
        self.assertEqual(row.config["url"], "http://old.example.test/mcp")
        self.assertEqual(row.inspection["status"], "healthy")

    async def test_rename_write_failure_rolls_back_mcp_and_agent_references(self) -> None:
        await self.stores.mcp.create_unique(
            McpServerCreate(
                name="atomic-rename-old",
                transport="url",
                config={"url": "http://old.example.test/mcp"},
                enabled=False,
            )
        )
        model_id = (await self.stores.llm_model.list_all())[0].id
        agent = await self.stores.agent.create_unique(
            AgentCreate(
                name="atomic-rename-agent",
                model_id=model_id,
                prompt="keep capability",
                capabilities=["builtin:general", "mcp:atomic-rename-old"],
            )
        )

        with patch.object(
            AsyncSession,
            "flush",
            new=AsyncMock(side_effect=RuntimeError("forced rename failure")),
        ):
            async with AsyncClient(transport=self._transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/mcp/atomic-rename-old/rename",
                    json={"new_name": "atomic-rename-new"},
                )

        self.assertEqual(response.status_code, 500, response.text)
        self.assertIsNotNone(await self.stores.mcp.get_by_name("atomic-rename-old"))
        self.assertIsNone(await self.stores.mcp.get_by_name("atomic-rename-new"))
        self.assertEqual(
            (await self.stores.agent.get_by_id(agent.id)).capabilities,
            ["builtin:general", "mcp:atomic-rename-old"],
        )


if __name__ == "__main__":
    unittest.main()
