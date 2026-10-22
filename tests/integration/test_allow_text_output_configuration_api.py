"""Trusted Agent API policy writes, readback and merged configuration behavior."""
from __future__ import annotations

import unittest

from tests.integration import test_agent_packages_api as fixture
from tests.unit.test_agent_output_configuration import OUTPUT_CONFIG


class AllowTextOutputConfigurationApiTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.AgentPackagesApiTests.asyncSetUp
    asyncTearDown = fixture.AgentPackagesApiTests.asyncTearDown

    async def test_default_readback_draft_patch_omission_false_and_null_rejection(self):
        created = await self.client.post("/api/v1/agents", headers=self.headers,
            json={"name": "policy fixture", "prompt": "synthetic"})
        self.assertEqual(created.status_code, 201, created.text)
        self.assertFalse(created.json()["allow_text_output"])
        url = f'/api/v1/agents/{created.json()["id"]}'
        changed = await self.client.patch(url, headers=self.headers,
            json={"allow_text_output": True, "output_config": OUTPUT_CONFIG})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertTrue(changed.json()["allow_text_output"])
        self.assertFalse(changed.json()["structured_output_enabled"])
        detail = await self.client.get(url + "/output-config", headers=self.headers)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertTrue(detail.json()["allow_text_output"])
        self.assertEqual(detail.json()["output_config"], OUTPUT_CONFIG)
        ordinary = await self.client.get(url, headers={"user": "ordinary-user"})
        self.assertEqual(ordinary.status_code, 200)
        self.assertTrue(ordinary.json()["allow_text_output"])
        self.assertNotIn("output_config", ordinary.json())
        renamed = await self.client.patch(url, headers=self.headers, json={"name": "preserved policy"})
        self.assertEqual(renamed.status_code, 200)
        self.assertTrue(renamed.json()["allow_text_output"])
        changed_definition = {**OUTPUT_CONFIG, "description": "edited while preserving text policy"}
        edited = await self.client.patch(url, headers=self.headers, json={"output_config": changed_definition})
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertTrue(edited.json()["allow_text_output"])
        self.assertEqual((await self.client.get(url + "/output-config", headers=self.headers)).json()["output_config"],
                         changed_definition)
        before = (await self.client.get(url, headers=self.headers)).json()
        for value in (None, "true", 1):
            invalid = await self.client.patch(url, headers=self.headers, json={"allow_text_output": value})
            self.assertEqual(invalid.status_code, 422, invalid.text)
            self.assertEqual((await self.client.get(url, headers=self.headers)).json(), before)
        enabled = await self.client.patch(url, headers=self.headers, json={"structured_output_enabled": True})
        self.assertEqual(enabled.status_code, 200)
        disabled = await self.client.patch(url, headers=self.headers, json={"structured_output_enabled": False})
        self.assertEqual(disabled.status_code, 200)
        self.assertTrue(disabled.json()["allow_text_output"])
        strict = await self.client.patch(url, headers=self.headers, json={"allow_text_output": False})
        self.assertEqual(strict.status_code, 200)
        self.assertFalse(strict.json()["allow_text_output"])
        self.assertFalse((await self.client.get(url + "/output-config", headers=self.headers)).json()["allow_text_output"])

    async def test_missing_definition_and_delete_policy_are_atomic(self):
        rejected = await self.client.post("/api/v1/agents", headers=self.headers,
            json={"name": "missing config", "prompt": "synthetic", "allow_text_output": True})
        self.assertEqual(rejected.status_code, 422, rejected.text)
        created = await self.client.post("/api/v1/agents", headers=self.headers,
            json={"name": "delete policy", "prompt": "synthetic"})
        self.assertEqual(created.status_code, 201, created.text)
        url = f'/api/v1/agents/{created.json()["id"]}'
        before = created.json()
        for changes in ({"allow_text_output": True},
                        {"allow_text_output": True, "output_config": None},
                        {"allow_text_output": True, "structured_output_enabled": True}):
            rejected = await self.client.patch(url, headers=self.headers, json=changes)
            self.assertEqual(rejected.status_code, 422, rejected.text)
            self.assertEqual((await self.client.get(url, headers=self.headers)).json(), before)
            self.assertEqual((await self.client.get(url + "/output-config", headers=self.headers)).json(),
                {"structured_output_enabled": False, "allow_text_output": False, "output_config": None})
        # Explicit false without a definition does not manufacture a config row.
        accepted = await self.client.patch(url, headers=self.headers, json={"allow_text_output": False})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertIsNone((await self.client.get(url + "/output-config", headers=self.headers)).json()["output_config"])
        accepted = await self.client.patch(url, headers=self.headers, json={"structured_output_enabled": True,
            "allow_text_output": True, "output_config": OUTPUT_CONFIG})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        before = accepted.json()
        for changes in ({"output_config": None},
                        {"structured_output_enabled": False, "output_config": None, "allow_text_output": True}):
            rejected = await self.client.patch(url, headers=self.headers, json=changes)
            self.assertEqual(rejected.status_code, 422, rejected.text)
            self.assertEqual((await self.client.get(url, headers=self.headers)).json(), before)
        removed = await self.client.patch(url, headers=self.headers,
            json={"structured_output_enabled": False, "output_config": None})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertFalse(removed.json()["allow_text_output"])
        self.assertEqual((await self.client.get(url + "/output-config", headers=self.headers)).json(),
            {"structured_output_enabled": False, "allow_text_output": False, "output_config": None})
