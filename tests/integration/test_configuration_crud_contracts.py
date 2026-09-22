from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.api.v1 import api_router
from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.capabilities.mcp.resources import build_mcp_resource_toolset
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.db.models import McpServer
from deep_agent.schemas.agent import AgentCreate
from deep_agent.schemas.mcp import McpServerCreate, McpServerUpdate
from deep_agent.schemas.ui_tools import UiToolPackageCreate
from tests.helpers.admin_auth import allow_console_admin
from tests.helpers.sqlite_stores import make_sqlite_stores


class ConfigurationCrudContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stores, self._engine, session_factory = await make_sqlite_stores()
        self._app = FastAPI()
        self._app.include_router(api_router)
        allow_console_admin(self._app)
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
        default_model = (await self.stores.llm_model.list_all())[0]
        self._model_id = default_model.id
        self._provider_id = default_model.provider_id
        await self.stores.ui_tool_package.create_manual(
            UiToolPackageCreate(
                code="ui:contracts",
                label="Contract tools",
                description="Integration-test UI tool package",
            )
        )

    async def asyncTearDown(self) -> None:
        await get_context().event_persister.aclose()
        reset_context()
        await self._engine.dispose()

    async def test_configuration_api_timestamps_use_explicit_utc_offsets(self) -> None:
        await self.stores.mcp.create_unique(
            McpServerCreate(
                name="utc_contract",
                transport="url",
                config={"url": "http://utc.example.test/mcp"},
            )
        )
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            web_search = await client.put(
                "/api/v1/global-settings/web-search",
                json={"value": {"mode": "provider", "provider": "duckduckgo"}},
            )
            responses = [
                await client.get("/api/v1/agents"),
                await client.get("/api/v1/llm-models"),
                await client.get("/api/v1/llm-providers"),
                await client.get("/api/v1/ui-tools"),
                await client.get("/api/v1/mcp"),
                web_search,
            ]

        timestamp_count = 0

        def assert_timestamp_contract(value) -> None:
            nonlocal timestamp_count
            if isinstance(value, dict):
                for key, child in value.items():
                    if key.endswith("_at") and child is not None:
                        timestamp_count += 1
                        self.assertIsInstance(child, str)
                        self.assertTrue(child.endswith("+00:00"), (key, child))
                    assert_timestamp_contract(child)
            elif isinstance(value, list):
                for child in value:
                    assert_timestamp_contract(child)

        for response in responses:
            self.assertEqual(response.status_code, 200, response.text)
            assert_timestamp_contract(response.json())
        self.assertGreaterEqual(timestamp_count, 8)

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
                    "context_window_tokens": 128_000,
                    "max_output_tokens": 8_192,
                },
            )
            ui_tool = await client.post(
                "/api/v1/ui-tools",
                json={
                    "name": "audit_blank",
                    "package_code": "ui:contracts",
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
                    "provider_id": self._provider_id,
                    "model": "test:contract",
                    "input_modalities": ["text"],
                    "context_window_tokens": 128_000,
                    "max_output_tokens": 8_192,
                },
            )
            tool_response = await client.post(
                "/api/v1/ui-tools",
                json={
                    "name": "contract_tool",
                    "package_code": "ui:contracts",
                    "description": "keep description",
                    "parameters_json_schema": {"type": "object"},
                },
            )
            self.assertEqual(agent_response.status_code, 201, agent_response.text)
            self.assertEqual(model_response.status_code, 201, model_response.text)
            self.assertEqual(tool_response.status_code, 201, tool_response.text)

            for field in ("name", "prompt"):
                response = await client.patch(
                    f"/api/v1/agents/{agent_response.json()['id']}",
                    json={field: None},
                )
                self.assertEqual(response.status_code, 422, response.text)
            response = await client.patch(
                f"/api/v1/agents/{agent_response.json()['id']}",
                json={"model_id": None},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIsNone(response.json()["model_id"])
            for field in ("name", "model"):
                response = await client.patch(
                    f"/api/v1/llm-models/{model_response.json()['id']}",
                    json={field: None},
                )
                self.assertEqual(response.status_code, 422, response.text)
            for field in ("package_code", "description", "parameters_json_schema", "enabled"):
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
        self.assertIsNone(agent_row.model_id)
        self.assertEqual(model_row.name, "contract-model")
        self.assertEqual(model_row.model, "test:contract")
        self.assertEqual(tool_row.description, "keep description")
        self.assertEqual(tool_row.parameters_json_schema, {"type": "object"})

    async def test_invalid_provider_base_url_and_result_schema_return_422(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            provider = await client.post(
                "/api/v1/llm-providers",
                json={
                    "name": "invalid-url",
                    "provider_type": "openai_compatible",
                    "use_default_base_url": False,
                    "base_url": "ftp://example.test",
                    "api_key": "secret",
                },
            )
            tool = await client.post(
                "/api/v1/ui-tools",
                json={
                    "name": "invalid_result",
                    "package_code": "ui:contracts",
                    "description": "invalid result",
                    "parameters_json_schema": {"type": "object"},
                    "result_schema": {"type": 123},
                },
            )

        self.assertEqual(provider.status_code, 422)
        self.assertEqual(tool.status_code, 422)

    async def test_api_key_can_be_cleared_explicitly(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            created = await client.post(
                "/api/v1/llm-providers",
                json={
                    "name": "clear-key",
                    "provider_type": "openai_compatible",
                    "use_default_base_url": False,
                    "base_url": "http://test.invalid/v1",
                    "api_key": "secret",
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            self.assertTrue(created.json()["api_key_set"])
            cleared = await client.patch(
                f"/api/v1/llm-providers/{created.json()['id']}",
                json={"clear_api_key": True},
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
                    "provider_id": self._provider_id,
                    "model": "test:duplicate",
                    "input_modalities": ["text"],
                    "context_window_tokens": 128_000,
                    "max_output_tokens": 8_192,
                },
            ),
            (
                "/api/v1/mcp",
                {
                    "name": "duplicate_mcp",
                    "transport": "url",
                    "config": {"url": "http://example.test/mcp"},
                },
            ),
            (
                "/api/v1/ui-tools",
                {
                    "name": "duplicate_tool",
                    "package_code": "ui:contracts",
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

    async def test_agent_rejects_unknown_and_accepts_configured_tool_packages(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            configured = await client.post(
                "/api/v1/ui-tool-packages",
                json={"code": "ui:configured", "label": "Configured"},
            )
            self.assertEqual(configured.status_code, 201, configured.text)
            tool = await client.post(
                "/api/v1/ui-tools",
                json={
                    "name": "configured__tool",
                    "package_code": "ui:configured",
                    "description": "Configured tool",
                    "parameters_json_schema": {"type": "object"},
                },
            )
            self.assertEqual(tool.status_code, 201, tool.text)
            missing = await client.post(
                "/api/v1/agents",
                json={
                    "name": "invalid-missing",
                    "model_id": self._model_id,
                    "prompt": "invalid package",
                    "capabilities": ["ui:missing"],
                },
            )
            accepted = await client.post(
                "/api/v1/agents",
                json={
                    "name": "valid-configured",
                    "model_id": self._model_id,
                    "prompt": "configured package",
                    "capabilities": ["ui:configured"],
                },
            )
            self.assertEqual(missing.status_code, 422, missing.text)
            self.assertEqual(
                missing.json()["detail"]["invalid_tool_packages"],
                ["ui:missing"],
            )
            self.assertEqual(accepted.status_code, 201, accepted.text)

    async def test_historical_stdio_mcp_is_readable_but_cannot_be_updated(self) -> None:
        async with self.stores.mcp._write() as db:
            db.add(
                McpServer(
                    name="historical_stdio",
                    transport="stdio",
                    config={"command": "python", "args": ["server.py"]},
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

    async def test_url_mcp_rename_migrates_agent_references_and_tool_directory(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            created = await client.post(
                "/api/v1/mcp",
                json={
                    "name": "rename_old",
                    "transport": "url",
                    "config": {"url": "http://example.test/mcp"},
                    "description": "keep description",
                },
            )
        self.assertEqual(created.status_code, 201, created.text)
        await self.stores.mcp.update(
            "rename_old",
            McpServerUpdate(),
            inspection={
                "status": "healthy",
                "tools": {
                    "status": "supported",
                    "count": 2,
                    "snapshot_status": "ready",
                    "snapshot_hash": "rename-snapshot",
                    "snapshot": {
                        "schema_version": "1.0",
                        "definitions": [
                            {"name": "query", "input_schema": {"type": "object"}},
                            {"name": "list", "input_schema": {"type": "object"}},
                        ],
                    },
                },
                "resources": {"status": "supported", "count": 1, "template_count": 0},
            },
        )
        first = await self.stores.agent.create_unique(
            AgentCreate(
                name="rename-agent-a",
                model_id=self._model_id,
                prompt="rename A",
                capabilities=["builtin:general", "mcp:rename_old", "ui:contracts"],
            )
        )
        second = await self.stores.agent.create_unique(
            AgentCreate(
                name="rename-agent-b",
                model_id=self._model_id,
                prompt="rename B",
                capabilities=["mcp:rename_old", "builtin:sandbox"],
            )
        )
        untouched_empty = await self.stores.agent.create_unique(
            AgentCreate(
                name="rename-agent-empty",
                model_id=self._model_id,
                prompt="empty",
                capabilities=[],
            )
        )

        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/mcp/rename_old/rename",
                json={"new_name": "rename_new"},
            )
            old_row = await client.get("/api/v1/mcp/rename_old")
            new_row = await client.get("/api/v1/mcp/rename_new")
            tools = await client.get("/api/v1/tools")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["old_name"], "rename_old")
        self.assertEqual(response.json()["new_name"], "rename_new")
        self.assertEqual(
            response.json()["updated_agents"],
            ["rename-agent-a", "rename-agent-b"],
        )
        self.assertEqual(response.json()["mcp"]["description"], "keep description")
        self.assertEqual(response.json()["mcp"]["inspection"]["tools"]["count"], 2)
        self.assertEqual(
            response.json()["mcp"]["inspection"]["tools"]["snapshot_hash"],
            "rename-snapshot",
        )
        self.assertNotIn("snapshot", response.json()["mcp"]["inspection"]["tools"])
        self.assertEqual(old_row.status_code, 404, old_row.text)
        self.assertEqual(new_row.status_code, 200, new_row.text)
        self.assertEqual(
            (await self.stores.agent.get_by_id(first.id)).capabilities,
            ["builtin:general", "mcp:rename_new", "ui:contracts"],
        )
        self.assertEqual(
            (await self.stores.agent.get_by_id(second.id)).capabilities,
            ["mcp:rename_new", "builtin:sandbox"],
        )
        self.assertEqual((await self.stores.agent.get_by_id(untouched_empty.id)).capabilities, [])
        tool_codes = {item["code"] for item in tools.json()}
        self.assertIn("mcp:rename_new", tool_codes)
        self.assertNotIn("mcp:rename_old", tool_codes)
        renamed_row = await self.stores.mcp.get_by_name("rename_new")
        self.assertIsNotNone(
            build_mcp_resource_toolset(
                AgentSpec(
                    name="resource-new",
                    model="test",
                    instructions="",
                    capabilities=["mcp:rename_new"],
                ),
                [renamed_row],
            )
        )
        self.assertIsNone(
            build_mcp_resource_toolset(
                AgentSpec(
                    name="resource-old",
                    model="test",
                    instructions="",
                    capabilities=["mcp:rename_old"],
                ),
                [renamed_row],
            )
        )

    async def test_mcp_rename_contract_errors_and_protected_transports(self) -> None:
        await self.stores.mcp.create_unique(
            McpServerCreate(
                name="rename_source",
                transport="url",
                config={"url": "http://source.example.test/mcp"},
            )
        )
        await self.stores.mcp.create_unique(
            McpServerCreate(
                name="rename_taken",
                transport="url",
                config={"url": "http://taken.example.test/mcp"},
            )
        )
        async with self.stores.mcp._write() as db:
            db.add_all(
                [
                    McpServer(name="rename_builtin", transport="builtin", config={}),
                    McpServer(
                        name="rename_stdio",
                        transport="stdio",
                        config={"command": "python", "args": ["server.py"]},
                    ),
                ]
            )

        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            conflict = await client.post(
                "/api/v1/mcp/rename_source/rename",
                json={"new_name": "rename_taken"},
            )
            invalid = await client.post(
                "/api/v1/mcp/rename_source/rename",
                json={"new_name": "invalid name"},
            )
            missing = await client.post(
                "/api/v1/mcp/rename_missing/rename",
                json={"new_name": "valid_name"},
            )
            builtin = await client.post(
                "/api/v1/mcp/rename_builtin/rename",
                json={"new_name": "builtin_new"},
            )
            stdio = await client.post(
                "/api/v1/mcp/rename_stdio/rename",
                json={"new_name": "stdio_new"},
            )

        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(invalid.status_code, 422, invalid.text)
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(builtin.status_code, 403, builtin.text)
        self.assertEqual(stdio.status_code, 403, stdio.text)
        self.assertIsNotNone(await self.stores.mcp.get_by_name("rename_source"))
        self.assertIsNotNone(await self.stores.mcp.get_by_name("rename_taken"))
        self.assertIsNotNone(await self.stores.mcp.get_by_name("rename_builtin"))
        self.assertIsNotNone(await self.stores.mcp.get_by_name("rename_stdio"))

    async def test_mcp_admin_list_prioritizes_custom_then_newest(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.stores.mcp._write() as db:
            db.add_all(
                [
                    McpServer(
                        name="custom_old",
                        transport="url",
                        config={"url": "http://old.example.test/mcp"},
                        created_at=now - timedelta(days=2),
                        updated_at=now - timedelta(days=2),
                    ),
                    McpServer(
                        name="custom_new",
                        transport="url",
                        config={"url": "http://new.example.test/mcp"},
                        created_at=now,
                        updated_at=now,
                    ),
                    McpServer(
                        name="builtin_old",
                        transport="builtin",
                        config={},
                        created_at=now - timedelta(days=3),
                        updated_at=now - timedelta(days=3),
                    ),
                    McpServer(
                        name="builtin_new",
                        transport="builtin",
                        config={},
                        created_at=now - timedelta(days=1),
                        updated_at=now - timedelta(days=1),
                    ),
                ]
            )

        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            response = await client.get("/api/v1/mcp")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [item["name"] for item in response.json()],
            ["custom_new", "custom_old", "builtin_new", "builtin_old"],
        )
        self.assertEqual(
            [row.name for row in await self.stores.mcp.list_all()],
            ["builtin_new", "builtin_old", "custom_new", "custom_old"],
        )

    async def test_referenced_mcp_and_ui_tool_cannot_be_deleted(self) -> None:
        async with AsyncClient(transport=self._transport, base_url="http://test") as client:
            mcp = await client.post(
                "/api/v1/mcp",
                json={
                    "name": "referenced_mcp",
                    "transport": "url",
                    "config": {"url": "http://example.test/mcp"},
                },
            )
            tool = await client.post(
                "/api/v1/ui-tools",
                json={
                    "name": "referenced_tool",
                    "package_code": "ui:contracts",
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
                        "ui:contracts",
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


if __name__ == "__main__":
    unittest.main()
