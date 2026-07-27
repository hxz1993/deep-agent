from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from deep_agent.api.v1 import api_router
from deep_agent.api.v1.global_settings import build_global_settings
from deep_agent.options import Agent as AgentOptions
from deep_agent.options import DeepAgentOptions


def _find_item(response, key: str):
    for group in response.groups:
        for item in group.items:
            if item.key == key:
                return item
    raise AssertionError(f"Missing setting: {key}")


class GlobalSettingsBuilderTests(unittest.TestCase):
    def test_uses_default_value_and_marks_default_source(self) -> None:
        previous_source = AgentOptions.max_requests.set_by
        try:
            AgentOptions.max_requests.set_by = None
            with patch.dict(
                os.environ,
                {"DEEPFOS_COMPONENT_TEST_MODE": "true"},
                clear=False,
            ):
                settings = build_global_settings(DeepAgentOptions())
        finally:
            AgentOptions.max_requests.set_by = previous_source

        item = _find_item(settings, "max_requests")
        self.assertEqual(item.value, 30)
        self.assertEqual(item.display_value, "30")
        self.assertEqual(item.source, "default")
        self.assertEqual(item.env_name, "DEEP_AGENT_MAX_REQUESTS")

    def test_marks_configured_value_as_environment_source(self) -> None:
        previous_source = AgentOptions.max_requests.set_by
        try:
            with patch.dict(
                os.environ,
                {"DEEPFOS_COMPONENT_TEST_MODE": "true"},
                clear=False,
            ):
                options = DeepAgentOptions()
                options.agent.max_requests = 44
                AgentOptions.max_requests.set_by = "DEEP_AGENT_MAX_REQUESTS"
                settings = build_global_settings(options)
        finally:
            AgentOptions.max_requests.set_by = previous_source

        item = _find_item(settings, "max_requests")
        self.assertEqual(item.value, 44)
        self.assertEqual(item.source, "environment")

    def test_database_type_uses_effective_database_backend(self) -> None:
        with patch(
            "deep_agent.api.v1.global_settings.get_database_url",
            return_value="sqlite:////tmp/deep-agent.db",
        ):
            settings = build_global_settings()

        item = _find_item(settings, "database_type")
        self.assertEqual(item.value, "sqlite")
        self.assertEqual(item.display_value, "sqlite")

    def test_whitelist_does_not_expose_secrets_or_paths(self) -> None:
        settings = build_global_settings()
        payload = settings.model_dump_json().lower()
        for forbidden in (
            "deep_agent_dbms_password",
            "deep_agent_dbms_hostname",
            "deep_agent_dbms_user",
            "deep_agent_dbms_database",
            "deep_agent_title_model_api_key",
            "deep_agent_title_model_base_url",
            "deep_agent_gemini_proxy",
            "deep_agent_sqlite_path",
            "deep_agent_mcp_runtime_dir",
        ):
            self.assertNotIn(forbidden, payload)

        self.assertEqual(
            [group.key for group in settings.groups],
            ["runtime", "mcp", "uploads", "images", "titles"],
        )


class GlobalSettingsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(api_router)
        self.client = TestClient(app)

    def test_returns_grouped_read_only_settings_without_extra_authentication(
        self,
    ) -> None:
        response = self.client.get("/api/v1/global-settings")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["groups"])
        first = body["groups"][0]["items"][0]
        self.assertEqual(
            set(first),
            {
                "key",
                "label",
                "env_name",
                "value",
                "display_value",
                "source",
                "unit",
                "description",
                "restart_required",
            },
        )
        self.assertTrue(first["restart_required"])


if __name__ == "__main__":
    unittest.main()
