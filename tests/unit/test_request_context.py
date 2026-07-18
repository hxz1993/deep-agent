from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from deep_agent.api.v1 import api_router


class RequestContextApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(api_router)
        self.client = TestClient(app)

    def test_returns_only_effective_non_sensitive_context(self) -> None:
        response = self.client.get(
            "/api/v1/request-context",
            headers={
                "user": "  test-user  ",
                "app": " app-a ",
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
                "app": "app-a",
                "space": "space-a",
                "language": "zh-cn",
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

    def test_optional_context_headers_normalize_to_null(self) -> None:
        response = self.client.get(
            "/api/v1/request-context",
            headers={"user": "user-a", "app": " ", "space": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["app"], None)
        self.assertEqual(response.json()["space"], None)
        self.assertEqual(response.json()["language"], None)

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
            },
        )
        for forbidden in ("authorization", "cookie", "token", "x-user-token"):
            self.assertNotIn((forbidden, "header"), parameters)


if __name__ == "__main__":
    unittest.main()
