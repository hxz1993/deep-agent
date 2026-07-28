from __future__ import annotations

import unittest

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.db.models import McpServer
from tests.helpers.sqlite_stores import make_sqlite_stores


class ConfigurationCrudContractTests(unittest.IsolatedAsyncioTestCase):
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
        self._model_id = (await self.stores.llm_model.list_all())[0].id

    async def asyncTearDown(self) -> None:
        await get_context().event_persister.aclose()
        reset_context()
        await self._engine.dispose()

    async def test_blank_required_values_are_rejected_without_creating_rows(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            agent = await client.post(
                "/api/v1/agents",
                json={
                    "name": "   ",
                    "model_id": self._model_id,
                    "prompt": "   ",
                },
            )
            model = await client.post(
                "/api/v1/llm-models",
                json={
                    "name": "   ",
                    "model": "   ",
                    "input_modalities": ["text"],
                },
            )
            ui_tool = await client.post(
                "/api/v1/ui-tools",
                json={
                    "name": "audit_blank",
                    "description": "   ",
                    "parameters_json_schema": {"type": "object"},
                },
            )
            mcp = await client.post(
                "/api/v1/mcp",
                json={
                    "name": "   ",
                    "transport": "url",
                    "config": {"url": "http://example.test/mcp"},
                    "enabled": False,
                },
            )

        self.assertEqual(agent.status_code, 422)
        self.assertEqual(model.status_code, 422)
        self.assertEqual(ui_tool.status_code, 422)
        self.assertEqual(mcp.status_code, 422)
        self.assertIsNone(await self.stores.agent.get_by_name(""))
        self.assertIsNone(await self.stores.llm_model.get_by_name(""))
        self.assertIsNone(await self.stores.ui_tool.get_by_name("audit_blank"))

    async def test_required_update_fields_reject_null_and_preserve_rows(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            agent_response = await client.post(
                "/api/v1/agents",
                json={
                    "name": "contract-agent",
                    "model_id": self._model_id,
                    "prompt": "keep prompt",
                },
            )
            model_response = await client.post(
                "/api/v1/llm-models",
                json={
                    "name": "contract-model",
                    "model": "test:contract",
                    "input_modalities": ["text"],
                },
            )
            tool_response = await client.post(
                "/api/v1/ui-tools",
                json={
                    "name": "contract_tool",
                    "description": "keep description",
                    "parameters_json_schema": {"type": "object"},
                    "enabled": True,
                },
            )
            self.assertEqual(agent_response.status_code, 201, agent_response.text)
            self.assertEqual(model_response.status_code, 201, model_response.text)
            self.assertEqual(tool_response.status_code, 201, tool_response.text)

            for field in ("name", "model_id", "prompt"):
                response = await client.patch(
                    f"/api/v1/agents/{agent_response.json()['id']}",
                    json={field: None},
                )
                self.assertEqual(response.status_code, 422, response.text)
            for field in ("name", "model"):
                response = await client.patch(
                    f"/api/v1/llm-models/{model_response.json()['id']}",
                    json={field: None},
                )
                self.assertEqual(response.status_code, 422, response.text)
            for field in ("description", "parameters_json_schema", "enabled"):
                response = await client.patch(
                    "/api/v1/ui-tools/contract_tool",
                    json={field: None},
                )
                self.assertEqual(response.status_code, 422, response.text)

        agent_row = await self.stores.agent.get_by_id(agent_response.json()["id"])
        model_row = await self.stores.llm_model.get_by_id(model_response.json()["id"])
        tool_row = await self.stores.ui_tool.get_by_name("contract_tool")
        self.assertEqual(agent_row.name, "contract-agent")
        self.assertEqual(agent_row.prompt, "keep prompt")
        self.assertEqual(model_row.name, "contract-model")
        self.assertEqual(model_row.model, "test:contract")
        self.assertEqual(tool_row.description, "keep description")
        self.assertEqual(tool_row.parameters_json_schema, {"type": "object"})
        self.assertTrue(tool_row.enabled)

    async def test_invalid_model_base_url_and_result_schema_return_422(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            model = await client.post(
                "/api/v1/llm-models",
                json={
                    "name": "invalid-url",
                    "model": "test:model",
                    "base_url": "ftp://example.test",
                    "input_modalities": ["text"],
                },
            )
            tool = await client.post(
                "/api/v1/ui-tools",
                json={
                    "name": "invalid_result",
                    "description": "invalid result",
                    "parameters_json_schema": {"type": "object"},
                    "result_schema": {"type": 123},
                },
            )

        self.assertEqual(model.status_code, 422)
        self.assertEqual(tool.status_code, 422)

    async def test_api_key_can_be_cleared_explicitly(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            created = await client.post(
                "/api/v1/llm-models",
                json={
                    "name": "clear-key",
                    "model": "test:model",
                    "api_key": "secret",
                    "input_modalities": ["text"],
                },
            )
            self.assertTrue(created.json()["api_key_set"])
            cleared = await client.patch(
                f"/api/v1/llm-models/{created.json()['id']}",
                json={"api_key": None},
            )

        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertFalse(cleared.json()["api_key_set"])

    async def test_duplicate_configuration_names_return_409(self) -> None:
        payloads = (
            (
                "/api/v1/agents",
                {
                    "name": "duplicate-agent",
                    "model_id": self._model_id,
                    "prompt": "duplicate",
                },
            ),
            (
                "/api/v1/llm-models",
                {
                    "name": "duplicate-model",
                    "model": "test:duplicate",
                    "input_modalities": ["text"],
                },
            ),
            (
                "/api/v1/mcp",
                {
                    "name": "duplicate_mcp",
                    "transport": "url",
                    "config": {"url": "http://example.test/mcp"},
                    "enabled": False,
                },
            ),
            (
                "/api/v1/ui-tools",
                {
                    "name": "duplicate_tool",
                    "description": "duplicate",
                    "parameters_json_schema": {"type": "object"},
                },
            ),
        )
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            for path, payload in payloads:
                with self.subTest(path=path):
                    created = await client.post(path, json=payload)
                    duplicate = await client.post(path, json=payload)
                    self.assertEqual(created.status_code, 201, created.text)
                    self.assertEqual(duplicate.status_code, 409, duplicate.text)

    async def test_historical_stdio_mcp_is_readable_but_cannot_be_updated(self) -> None:
        async with self.stores.mcp._write() as db:
            db.add(
                McpServer(
                    name="historical_stdio",
                    transport="stdio",
                    config={"command": "python", "args": ["server.py"]},
                    enabled=False,
                )
            )

        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            listed = await client.get("/api/v1/mcp")
            fetched = await client.get("/api/v1/mcp/historical_stdio")
            updated = await client.patch(
                "/api/v1/mcp/historical_stdio",
                json={"description": "must remain unchanged"},
            )

        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertIn("historical_stdio", {item["name"] for item in listed.json()})
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["transport"], "stdio")
        self.assertEqual(updated.status_code, 409, updated.text)
        self.assertIsNone((await self.stores.mcp.get_by_name("historical_stdio")).description)

    async def test_referenced_mcp_and_ui_tool_cannot_be_deleted(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            mcp = await client.post(
                "/api/v1/mcp",
                json={
                    "name": "referenced_mcp",
                    "transport": "url",
                    "config": {"url": "http://example.test/mcp"},
                    "enabled": False,
                },
            )
            tool = await client.post(
                "/api/v1/ui-tools",
                json={
                    "name": "referenced_tool",
                    "description": "referenced",
                    "parameters_json_schema": {"type": "object"},
                },
            )
            agent = await client.post(
                "/api/v1/agents",
                json={
                    "name": "reference-owner",
                    "model_id": self._model_id,
                    "prompt": "owner",
                    "capabilities": [
                        "mcp:referenced_mcp",
                        "ui:referenced_tool",
                    ],
                },
            )
            self.assertEqual(mcp.status_code, 201, mcp.text)
            self.assertEqual(tool.status_code, 201, tool.text)
            self.assertEqual(agent.status_code, 201, agent.text)

            mcp_delete = await client.delete("/api/v1/mcp/referenced_mcp")
            tool_delete = await client.delete("/api/v1/ui-tools/referenced_tool")

        self.assertEqual(mcp_delete.status_code, 409, mcp_delete.text)
        self.assertEqual(tool_delete.status_code, 409, tool_delete.text)
        self.assertEqual(mcp_delete.json()["detail"]["referenced_by"], ["reference-owner"])
        self.assertEqual(tool_delete.json()["detail"]["referenced_by"], ["reference-owner"])
        self.assertIsNotNone(await self.stores.mcp.get_by_name("referenced_mcp"))
        self.assertIsNotNone(await self.stores.ui_tool.get_by_name("referenced_tool"))

    async def test_builtin_mcp_cannot_be_deleted(self) -> None:
        async with self.stores.mcp._write() as db:
            db.add(
                McpServer(
                    name="builtin_protected",
                    transport="builtin",
                    config={},
                    enabled=True,
                )
            )

        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.delete("/api/v1/mcp/builtin_protected")

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(
            response.json()["detail"],
            "内建 MCP 由程序管理，不能通过 API 删除",
        )
        self.assertIsNotNone(await self.stores.mcp.get_by_name("builtin_protected"))

    async def test_auxiliary_update_creates_missing_default_row(self) -> None:
        async with self.stores.auxiliary_model._write() as db:
            row = await self.stores.auxiliary_model._get_by_use_case(db, "title")
            await db.delete(row)

        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.patch(
                "/api/v1/auxiliary-models/title",
                json={"llm_model_id": self._model_id},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["llm_model_id"], self._model_id)


if __name__ == "__main__":
    unittest.main()
