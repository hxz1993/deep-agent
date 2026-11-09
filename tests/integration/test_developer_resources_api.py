from __future__ import annotations

import hashlib
import unittest
from types import SimpleNamespace
from urllib.parse import unquote
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from deep_agent.api.v1.developer_resources import router
from deep_agent.core.context import get_app_context
from deep_agent.developer_resources import ResourceUnavailableError
from deep_agent.security import ConsoleAdminAuthenticationError, ConsoleAdminAuthorizer


class DeveloperResourceApiTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api/v1")
        self.checker = AsyncMock(return_value=True)
        context = SimpleNamespace(console_admin_authorizer=ConsoleAdminAuthorizer(self.checker))
        self.app.dependency_overrides[get_app_context] = lambda: context
        self.client = self.enterContext(TestClient(self.app))
        self.headers = {"user": "developer-user", "Authorization": "Bearer test-only"}

    def test_admin_list_and_download_return_matching_files(self):
        response = self.client.get("/api/v1/developer-resources", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        items = response.json()["items"]
        self.assertEqual(len(items), 2)
        for item in items:
            with self.subTest(code=item["code"]):
                download = self.client.get("/" + item["download_url"], headers=self.headers)
                self.assertEqual(download.status_code, 200)
                self.assertEqual(download.headers["content-type"], item["media_type"])
                self.assertEqual(len(download.content), item["size_bytes"])
                self.assertEqual(hashlib.sha256(download.content).hexdigest(), item["sha256"])
                self.assertIn(item["filename"], unquote(download.headers["content-disposition"]))
                self.assertEqual(download.headers["x-content-type-options"], "nosniff")
                self.assertEqual(download.headers["cache-control"], "no-store")
        self.checker.assert_awaited_once()

    def test_unauthenticated_requests_do_not_read_files(self):
        with patch("deep_agent.api.v1.developer_resources.list_resources") as listing, patch(
            "deep_agent.api.v1.developer_resources.read_resource"
        ) as reading:
            for path in ("/developer-resources", "/developer-resources/development-skill/download"):
                with self.subTest(path=path):
                    self.assertEqual(self.client.get("/api/v1" + path).status_code, 401)
            listing.assert_not_called()
            reading.assert_not_called()
        self.checker.assert_not_awaited()

    def test_non_admin_is_denied_for_catalog_and_download(self):
        self.checker.return_value = False
        for path in ("/developer-resources", "/developer-resources/initialization-guide/download"):
            with self.subTest(path=path):
                result = self.client.get("/api/v1" + path, headers=self.headers)
                self.assertEqual(result.status_code, 403)

    def test_expired_platform_auth_is_401(self):
        self.checker.side_effect = ConsoleAdminAuthenticationError("invalid")
        result = self.client.get("/api/v1/developer-resources", headers=self.headers)
        self.assertEqual(result.status_code, 401)

    def test_bad_code_and_unavailable_resources_are_not_downloaded_as_success(self):
        response = self.client.get("/api/v1/developer-resources/not-a-resource/download", headers=self.headers)
        self.assertEqual(response.status_code, 404)
        for endpoint, target in (
            ("/developer-resources", "list_resources"),
            ("/developer-resources/development-skill/download", "read_resource"),
        ):
            with self.subTest(endpoint=endpoint), patch(
                f"deep_agent.api.v1.developer_resources.{target}",
                side_effect=ResourceUnavailableError("开发资料暂不可用，请联系管理员检查发行文件"),
            ):
                response = self.client.get("/api/v1" + endpoint, headers=self.headers)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.headers["x-deepagent-error"], "developer_resources_unavailable")
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertNotIn("content-disposition", response.headers)
                self.assertIn("开发资料暂不可用", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
