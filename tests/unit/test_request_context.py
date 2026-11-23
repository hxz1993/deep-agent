from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from deep_agent.api.v1 import api_router
from deep_agent.services.scope_names import ScopeDirectory


class RequestContextApiTests(unittest.TestCase):
    def setUp(self) -> None:
        directory_patch = patch(
            "deep_agent.api.v1.request_context.resolve_scope_directory",
            AsyncMock(return_value=ScopeDirectory()),
        )
        self.directory = directory_patch.start()
        self.addCleanup(directory_patch.stop)
        app = FastAPI()
        app.include_router(api_router)
        self.client = TestClient(app)

    def test_returns_only_effective_non_sensitive_context(self) -> None:
        response = self.client.get(
            "/api/v1/request-context",
            headers={
                "user": "  test-user  ",
                "app": " space-a001 ",
                "space": " space-a ",
                "language": " zh-cn ",
                "authorization": "Bearer secret",
                "cookie": "deepfos_token=secret",
                "x-user-token": "secret",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "authenticated": True,
                "user": "test-user",
                "app": "space-a001",
                "space": "space-a",
                "language": "zh-cn",
                "app_name": None,
                "space_name": None,
            },
        )

    def test_missing_user_is_rejected(self) -> None:
        self.assertEqual(self.client.get("/api/v1/request-context").status_code, 401)
        self.assertEqual(
            self.client.get(
                "/api/v1/request-context",
                headers={"user": "   "},
            ).status_code,
            401,
        )

    def test_names_enrich_effective_override_without_changing_request_language(self) -> None:
        self.directory.return_value = ScopeDirectory(
            apps={"budget001": "年度预算", "old001": "旧应用"},
            spaces={"budget": "预算空间", "old": "旧空间"},
        )
        response = self.client.get("/api/v1/request-context", headers={
            "user": "current-user", "app": "old001", "space": "old",
            "X-Deep-Agent-App": "budget001", "language": "en",
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual((data["app"], data["app_name"]), ("budget001", "年度预算"))
        self.assertEqual((data["space"], data["space_name"]), ("budget", "预算空间"))
        self.assertEqual(data["language"], "en")
        self.assertEqual(self.directory.await_count, 1)
        self.assertEqual(self.directory.call_args.args[1], "current-user")

    def test_deleted_or_unavailable_directory_keeps_codes_and_null_names(self) -> None:
        response = self.client.get("/api/v1/request-context", headers={
            "user": "current-user", "app": "deleted001",
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual((data["app"], data["space"]), ("deleted001", "deleted"))
        self.assertIsNone(data["app_name"])
        self.assertIsNone(data["space_name"])

    def test_optional_context_headers_normalize_to_null(self) -> None:
        response = self.client.get(
            "/api/v1/request-context",
            headers={"user": "user-a", "app": " ", "space": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["app"], None)
        self.assertEqual(response.json()["space"], None)
        self.assertEqual(response.json()["language"], "zh-cn")

    def test_app_only_context_derives_space_and_defaults_language(self) -> None:
        response = self.client.get(
            "/api/v1/request-context",
            headers={"user": "user-a", "app": "mdxath003"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "authenticated": True,
                "user": "user-a",
                "app": "mdxath003",
                "space": "mdxath",
                "language": "zh-cn",
                "app_name": None,
                "space_name": None,
            },
        )

    def test_console_override_uses_gateway_safe_headers(self) -> None:
        response = self.client.get(
            "/api/v1/request-context",
            headers={
                "user": "user-a",
                "app": "platform001",
                "space": "platform",
                "x-deep-agent-app": "iirjgi005",
                "x-deep-agent-language": "zh-cn",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "authenticated": True,
                "user": "user-a",
                "app": "iirjgi005",
                "space": "iirjgi",
                "language": "zh-cn",
                "app_name": None,
                "space_name": None,
            },
        )

    def test_mismatched_space_is_rejected(self) -> None:
        response = self.client.get(
            "/api/v1/request-context",
            headers={
                "user": "user-a",
                "app": "mdxath003",
                "space": "other",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("mdxath", response.json()["detail"])

    def test_openapi_exposes_no_sensitive_context_parameters(self) -> None:
        operation = self.client.get("/openapi.json").json()["paths"][
            "/api/v1/request-context"
        ]["get"]
        parameters = {(item["name"].lower(), item["in"]) for item in operation["parameters"]}

        self.assertEqual(
            parameters,
            {
                ("user", "header"),
                ("app", "header"),
                ("space", "header"),
                ("language", "header"),
                ("x-deep-agent-app", "header"),
                ("x-deep-agent-space", "header"),
                ("x-deep-agent-language", "header"),
            },
        )
        for forbidden in ("authorization", "cookie", "token", "x-user-token"):
            self.assertNotIn((forbidden, "header"), parameters)


if __name__ == "__main__":
    unittest.main()
