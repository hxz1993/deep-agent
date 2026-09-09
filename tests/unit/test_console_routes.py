from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from deep_agent.core.paths import CONSOLE_ROOT, REPO_ROOT
from deep_agent.main import app


class ConsoleRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_root_redirects_to_console_workspace(self) -> None:
        response = self.client.get("/", follow_redirects=False)

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/console/#/workspace")

    def test_console_vendor_and_api_docs_remain_available(self) -> None:
        self.assertEqual(self.client.get("/console/").status_code, 200)
        self.assertEqual(self.client.get("/vendor/lucide.min.js").status_code, 200)
        self.assertEqual(self.client.get("/docs").status_code, 200)
        self.assertEqual(self.client.get("/docs/").status_code, 200)
        self.assertEqual(
            self.client.get("/docs/oauth2-redirect").status_code,
            200,
        )
        self.assertEqual(self.client.get("/openapi.json").status_code, 200)
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_api_docs_preserve_a_reverse_proxy_service_prefix(self) -> None:
        for path in ("/docs", "/docs/"):
            with self.subTest(path=path):
                html = self.client.get(path).text
                self.assertIn(
                    'window.location.pathname.replace(/\\/docs\\/?$/, "")',
                    html,
                )
                self.assertIn("`${serviceBasePath}/openapi.json`", html)
                self.assertIn(
                    "`${window.location.origin}${serviceBasePath}"
                    "/docs/oauth2-redirect`",
                    html,
                )
                self.assertNotIn("url: '/openapi.json'", html)

    def test_console_without_trailing_slash_uses_proxy_safe_redirect(self) -> None:
        response = self.client.get("/console", follow_redirects=False)

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "./console/")

    def test_legacy_page_routes_are_removed(self) -> None:
        for path in ("/settings.html", "/models.html", "/admin", "/admin/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_console_has_no_runtime_dependency_on_legacy_pages(self) -> None:
        self.assertFalse((REPO_ROOT / "frontend").exists())
        runtime_files = [
            path
            for path in CONSOLE_ROOT.rglob("*")
            if path.is_file() and path.suffix in {".html", ".css", ".js"}
            and "vendor" not in path.relative_to(CONSOLE_ROOT).parts
        ]
        self.assertTrue(runtime_files)
        runtime_source = "\n".join(path.read_text(encoding="utf-8") for path in runtime_files)
        for reference in ("../index.html", "../models.html", "../admin/"):
            with self.subTest(reference=reference):
                self.assertNotIn(reference, runtime_source)

    def test_mcp_ui_discovery_surface_is_removed(self) -> None:
        runtime_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in CONSOLE_ROOT.rglob("*")
            if path.is_file() and path.suffix in {".html", ".css", ".js"}
            and "vendor" not in path.relative_to(CONSOLE_ROOT).parts
        )
        self.assertTrue(runtime_source)
        for removed in (
            "ui_tools_list",
            "ui://tools/manifest",
            "refresh_seconds",
            "/ui-tools/refresh",
            "启用 UI 发现",
            "刷新 UI",
        ):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, runtime_source)

        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertNotIn("/api/v1/mcp/{name}/ui-tools/refresh", paths)
        self.assertNotIn("/api/v1/mcp/{name}/resources/probe", paths)
        self.assertIn("/api/v1/mcp/{name}/inspect", paths)
        self.assertIn("/api/v1/mcp/{name}/rename", paths)


if __name__ == "__main__":
    unittest.main()
