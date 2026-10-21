"""Administrator output configuration and ordinary Agent initialization boundary."""
from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from deep_agent.security.diagnostic_users import DiagnosticUserAuthenticationError
from tests.integration import test_agent_packages_api as package_api
from tests.unit.test_agent_output_configuration import OUTPUT_CONFIG


class AgentOutputConfigurationApiTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = package_api.AgentPackagesApiTests.asyncSetUp
    asyncTearDown = package_api.AgentPackagesApiTests.asyncTearDown

    async def create(self, **changes):
        body = {"name": "output HTTP fixture", "prompt": "test", **changes}
        result = await self.client.post("/api/v1/agents", headers=self.headers, json=body)
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()

    async def test_atomic_create_patch_config_read_and_plain_detail(self):
        created = await self.create(structured_output_enabled=True, output_config=OUTPUT_CONFIG)
        self.assertEqual(created["updated_by"], "package-admin")
        self.assertNotIn("output_config", created)
        url = f'/api/v1/agents/{created["id"]}'
        detail = await self.client.get(url + "/output-config", headers=self.headers)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertTrue(detail.json()["structured_output_enabled"])
        self.assertEqual(detail.json()["output_config"], OUTPUT_CONFIG)
        with patch("deep_agent.api.v1.agents.resolve_diagnostic_users", side_effect=AssertionError("ordinary initialization must not look up accounts")):
            self.checker.return_value = False
            ordinary = await self.client.get(url, headers={"user": "ordinary-user"})
            self.assertEqual(ordinary.status_code, 200, ordinary.text)
            self.assertNotIn("output_config", ordinary.json())
            denied = await self.client.get(url + "/output-config", headers={"user": "ordinary-user"})
            self.assertEqual(denied.status_code, 403, denied.text)

    async def test_merged_delete_is_422_and_original_actor_unchanged(self):
        created = await self.create(structured_output_enabled=True, output_config=OUTPUT_CONFIG)
        url = f'/api/v1/agents/{created["id"]}'
        for body in ({"output_config": None}, {"structured_output_enabled": True, "output_config": None}):
            result = await self.client.patch(url, headers=self.headers, json=body)
            self.assertEqual(result.status_code, 422, result.text)
        self.assertEqual((await self.client.get(url, headers=self.headers)).json(), created)
        disabled = await self.client.patch(url, headers=self.headers, json={"structured_output_enabled": False})
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertIsNotNone((await self.client.get(url + "/output-config", headers=self.headers)).json()["output_config"])
        deleted = await self.client.patch(url, headers=self.headers, json={"output_config": None})
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertIsNone((await self.client.get(url + "/output-config", headers=self.headers)).json()["output_config"])

    async def test_invalid_schema_and_forged_actor_leave_zero_agent_writes(self):
        for fields in ({"updated_by": "forged"}, {"updated_by_user": {"username": "forged"}},
                       {"structured_output_enabled": "true"}, {"structured_output_enabled": None},
                       {"structured_output_enabled": True},
                       {"output_config": {**OUTPUT_CONFIG, "json_schema": {"$ref": "https://invalid.example/remote"}}}):
            with self.subTest(fields=fields):
                result = await self.client.post("/api/v1/agents", headers=self.headers,
                    json={"name": "invalid fixture", "prompt": "test", **fields})
                self.assertEqual(result.status_code, 422, result.text)
        self.assertEqual(await self.stores.agent.list_all(), [])

    async def test_username_batch_resolution_and_authentication_rejection(self):
        created = await self.create()
        profiles = AsyncMock(return_value={"package-admin": {
            "id": "package-admin", "username": "operator-name", "nickname": "not-the-username", "resolution": "resolved"}})
        with patch("deep_agent.api.v1.agents.resolve_diagnostic_users", profiles):
            listing = await self.client.get("/api/v1/agents", headers=self.headers)
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(listing.json()[0]["updated_by_user"], {
            "id": "package-admin", "username": "operator-name", "resolution": "resolved"})
        self.assertEqual(profiles.await_count, 1)
        with patch("deep_agent.api.v1.agents.resolve_diagnostic_users", side_effect=DiagnosticUserAuthenticationError(403)):
            denied = await self.client.get("/api/v1/agents", headers=self.headers)
        self.assertEqual(denied.status_code, 403, denied.text)

    async def test_install_records_target_operator_and_ignores_zip_source_actor(self):
        from deep_agent.agent_packages import archive
        from tests.unit.test_agent_packages import package_files
        raw = archive.build_zip(package_files(agent_changes={
            "structured_output_enabled": True, "output_config": OUTPUT_CONFIG, "updated_by": "forged-source"}))
        files = {"file": ("agent.zip", raw, "application/zip")}
        preview = await self.client.post("/api/v1/agent-packages/preview", headers=self.headers, files=files)
        self.assertEqual(preview.status_code, 200, preview.text)
        installed = await self.client.post("/api/v1/agent-packages/install", headers=self.headers, files=files,
            data={"options": json.dumps({"digest": preview.json()["digest"], "name": "installed output", "model_id": None})})
        self.assertEqual(installed.status_code, 201, installed.text)
        self.assertEqual(installed.json()["agent"]["updated_by"], "package-admin")
        config = await self.client.get(f'/api/v1/agents/{installed.json()["agent"]["id"]}/output-config', headers=self.headers)
        self.assertEqual(config.json()["output_config"], OUTPUT_CONFIG)

    async def test_known_output_tool_name_collision_is_rejected_before_save(self):
        from deep_agent.capabilities import build_registry
        self.ctx.registry = build_registry()
        package = self.ctx.registry.get("builtin:general")
        known_tool_name = package.tools[0].name
        collision = {**OUTPUT_CONFIG, "name": known_tool_name}
        response = await self.client.post("/api/v1/agents", headers=self.headers, json={
            "name": "collision fixture", "prompt": "test", "capabilities": ["builtin:general"],
            "structured_output_enabled": True, "output_config": collision,
        })
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(await self.stores.agent.list_all(), [])

    async def test_duplicate_name_and_unavailable_model_do_not_partially_update_output(self):
        from deep_agent.db.models import LlmModel
        created = await self.create(structured_output_enabled=True, output_config=OUTPUT_CONFIG)
        taken = await self.create(name="already taken")
        url = f'/api/v1/agents/{created["id"]}'
        changed = {**OUTPUT_CONFIG, "name": "different_result"}
        duplicate = await self.client.patch(url, headers={**self.headers, "user": "failed-writer"}, json={
            "name": taken["name"], "structured_output_enabled": False, "output_config": changed})
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        self.assertEqual((await self.client.get(url, headers=self.headers)).json(), created)
        self.assertEqual((await self.client.get(url + "/output-config", headers=self.headers)).json()["output_config"], OUTPUT_CONFIG)
        model = (await self.stores.llm_model.list_all())[0]
        async with self.stores.agent._write() as db:
            (await db.get(LlmModel, model.id)).enabled = False
        unavailable = await self.client.patch(url, headers={**self.headers, "user": "failed-writer"}, json={
            "name": "must remain unchanged", "model_id": model.id,
            "structured_output_enabled": False, "output_config": changed})
        self.assertEqual(unavailable.status_code, 422, unavailable.text)
        self.assertEqual((await self.client.get(url, headers=self.headers)).json(), created)
        self.assertEqual((await self.client.get(url + "/output-config", headers=self.headers)).json()["output_config"], OUTPUT_CONFIG)
