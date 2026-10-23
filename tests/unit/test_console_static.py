from __future__ import annotations

import os
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from deep_agent.core.console_static import ConsoleStaticFiles, _MAX_CACHED_BYTES
from deep_agent.main import prevent_static_error_cache


class ConsoleStaticCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="TMP_to_delete-console-cache-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.root.joinpath("index.html").write_text("<html>entry</html>", encoding="utf-8")
        self.root.joinpath("configuration-template.html").write_text("<section>配置</section>", encoding="utf-8")
        self.root.joinpath("module.js").write_text("const value = 'old';", encoding="utf-8")
        self.root.joinpath("404.html").write_text("<html>missing</html>", encoding="utf-8")
        self.root.joinpath("downloads").mkdir()
        self.root.joinpath("downloads", "guide.pdf").write_bytes(b"%PDF-" + b"0123456789" * 100)
        self.root.joinpath("downloads", "export.json").write_text('{"saved": true}', encoding="utf-8")
        self.app = FastAPI()
        self.app.middleware("http")(prevent_static_error_cache)

        @self.app.get("/api/data")
        async def api_data():
            return {"live": True}

        self.app.mount("/console", ConsoleStaticFiles(directory=self.root, html=True))
        self.app.mount("/vendor", ConsoleStaticFiles(directory=self.root))
        self.app.mount("/baseline", StaticFiles(directory=self.root, html=True))
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def test_entry_is_not_cached_or_conditionally_reused(self) -> None:
        for path in ("/console/", "/console/index.html"):
            with self.subTest(path=path):
                result = self.client.get(path, headers={"If-None-Match": "*"})
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.headers["cache-control"], "no-store")
                self.assertNotIn("etag", result.headers)
                head = self.client.head(path)
                self.assertEqual(head.status_code, 200)
                self.assertEqual(head.content, b"")
                self.assertEqual(head.headers["content-length"], str(len(result.content)))

    def test_assets_and_templates_are_content_validated_on_both_mounts(self) -> None:
        for path in ("/console/module.js?v=old", "/console/configuration-template.html", "/vendor/module.js"):
            with self.subTest(path=path):
                result = self.client.get(path)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.headers["cache-control"], "private, no-cache")
                self.assertTrue(result.headers["etag"].startswith('"sha256-'))
                self.assertNotIn("last-modified", result.headers)
                for method in (self.client.get, self.client.head):
                    cached = method(path, headers={"If-None-Match": result.headers["etag"]})
                    self.assertEqual(cached.status_code, 304)
                    self.assertEqual(cached.content, b"")
                    self.assertEqual(cached.headers["etag"], result.headers["etag"])
                    self.assertEqual(cached.headers["cache-control"], "private, no-cache")
                    self.assertNotIn("content-length", cached.headers)

    def test_content_changes_even_when_size_and_timestamp_are_preserved(self) -> None:
        path = self.root / "module.js"
        original_stat = path.stat()
        before = self.client.get("/console/module.js?v=fixed")
        path.write_text("const value = 'new';", encoding="utf-8")
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        self.assertEqual(path.stat().st_size, original_stat.st_size)
        after = self.client.get("/console/module.js?v=fixed", headers={
            "If-None-Match": before.headers["etag"],
            "If-Modified-Since": "Thu, 01 Jan 2099 00:00:00 GMT",
        })
        self.assertEqual(after.status_code, 200)
        self.assertNotEqual(after.headers["etag"], before.headers["etag"])
        self.assertIn("'new'", after.text)
        self.assertEqual(int(after.headers["content-length"]), len(after.content))

    def test_weak_list_wildcard_and_mismatched_validators(self) -> None:
        etag = self.client.get("/console/module.js").headers["etag"]
        for validator in (f"W/{etag}", f'"other", W/{etag}', "*"):
            with self.subTest(validator=validator):
                self.assertEqual(self.client.get("/console/module.js", headers={"If-None-Match": validator}).status_code, 304)
        for headers in (
            {"If-None-Match": '"other"', "If-Modified-Since": "Thu, 01 Jan 2099 00:00:00 GMT"},
            {"If-Modified-Since": "Thu, 01 Jan 2099 00:00:00 GMT"},
        ):
            with self.subTest(headers=headers):
                self.assertEqual(self.client.get("/console/module.js", headers=headers).status_code, 200)
        head = self.client.head("/console/module.js", headers={"If-None-Match": '"other"'})
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertGreater(int(head.headers["content-length"]), 0)

    def test_static_errors_are_not_cached_and_api_is_not_given_resource_headers(self) -> None:
        for method, path, expected in (
            (self.client.get, "/console/missing.js", 404),
            (self.client.get, "/vendor/missing.js", 404),
            (self.client.post, "/console/module.js", 405),
            (self.client.get, "/console/%2e%2e/outside.txt", 404),
        ):
            with self.subTest(path=path, method=method):
                result = method(path, headers={"If-None-Match": "*"})
                self.assertEqual(result.status_code, expected)
                self.assertEqual(result.headers["cache-control"], "no-store")
        api = self.client.get("/api/data", headers={"If-None-Match": "*"})
        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.json(), {"live": True})
        self.assertNotIn("cache-control", api.headers)
        self.assertNotIn("etag", api.headers)

    def test_reverse_proxy_root_path_and_symlink_containment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-console-outside-") as outside:
            target = Path(outside) / "private.txt"
            target.write_text("private", encoding="utf-8")
            (self.root / "escape.txt").symlink_to(target)
            denied = self.client.get("/console/escape.txt")
            self.assertEqual(denied.status_code, 404)
            self.assertNotIn("private", denied.text)
        prefix = "/deepfos-server/deep-agent-server1-0"
        with TestClient(self.app, root_path=prefix) as proxy:
            result = proxy.get(prefix + "/console/module.js")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.headers["cache-control"], "private, no-cache")
            denied = proxy.get(prefix + "/vendor/missing.js")
            self.assertEqual(denied.status_code, 404)
            self.assertEqual(denied.headers["cache-control"], "no-store")

    def test_downloads_and_oversized_assets_remain_native_file_responses(self) -> None:
        oversized = self.root / "large.js"
        with oversized.open("wb") as target:
            target.truncate(_MAX_CACHED_BYTES + 1)
        self.root.joinpath("bundle.zip").write_bytes(b"PK\x03\x04fixture")
        static = ConsoleStaticFiles(directory=self.root, html=True)
        for path in ("downloads/guide.pdf", "downloads/export.json", "bundle.zip", "large.js"):
            with self.subTest(path=path):
                scope = {"type": "http", "method": "GET", "path": path, "headers": []}
                with patch("deep_agent.core.console_static._read_content", side_effect=AssertionError("must remain streamed")):
                    response = asyncio.run(static.get_response(path, scope))
                self.assertIsInstance(response, FileResponse)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(response.headers["content-length"], str((self.root / path).stat().st_size))

    def test_range_if_range_and_head_preserve_installed_staticfiles_behavior(self) -> None:
        # Compare against the installed framework rather than assuming support:
        # Starlette 0.37.2 ignores Range; later FileResponse versions support it.
        for path in ("downloads/guide.pdf", "module.js"):
            baseline = self.client.get("/baseline/" + path)
            etag = baseline.headers["etag"]
            for method in ("GET", "HEAD"):
                for headers in (
                    {"Range": "bytes=0-9"},
                    {"Range": "bytes=0-9", "If-Range": etag},
                    {"Range": "bytes=0-9", "If-Range": '"other"'},
                    {"Range": "bytes=-5"},
                    {"Range": "bytes=999999-"},
                    {"Range": "bytes=0-2,5-8"},
                    {"Range": "invalid"},
                ):
                    with self.subTest(path=path, method=method, headers=headers):
                        expected = self.client.request(method, "/baseline/" + path, headers=headers)
                        with patch("deep_agent.core.console_static._read_content", side_effect=AssertionError("must preserve native range")):
                            actual = self.client.request(method, "/console/" + path, headers=headers)
                        self.assertEqual(actual.status_code, expected.status_code)
                        expected_body, actual_body = expected.content, actual.content
                        # Frameworks with multi-range support generate a new
                        # multipart boundary per request; compare its payload.
                        if "boundary=" in expected.headers.get("content-type", ""):
                            expected_boundary = expected.headers["content-type"].split("boundary=", 1)[1].strip('"')
                            actual_boundary = actual.headers["content-type"].split("boundary=", 1)[1].strip('"')
                            expected_body = expected_body.replace(expected_boundary.encode(), b"BOUNDARY")
                            actual_body = actual_body.replace(actual_boundary.encode(), b"BOUNDARY")
                        self.assertEqual(actual_body, expected_body)
                        for name in ("content-range", "content-length", "accept-ranges", "etag"):
                            self.assertEqual(actual.headers.get(name), expected.headers.get(name))
                        self.assertEqual(actual.headers["cache-control"], "no-store")

    def test_native_download_conditionals_and_static_redirects_remain_uncached(self) -> None:
        for path in ("/console/downloads/guide.pdf", "/vendor/downloads/guide.pdf"):
            original = self.client.get(path)
            cached = self.client.get(path, headers={"If-None-Match": original.headers["etag"]})
            self.assertEqual(cached.status_code, 304)
            self.assertEqual(cached.headers["cache-control"], "no-store")
            self.assertEqual(cached.content, b"")
            self.assertNotIn("content-length", cached.headers)
        directory = self.root / "nested"
        directory.mkdir()
        directory.joinpath("index.html").write_text("nested", encoding="utf-8")
        redirect = self.client.get("/console/nested", follow_redirects=False)
        self.assertEqual(redirect.status_code, 307)
        self.assertEqual(redirect.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
