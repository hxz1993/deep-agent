from __future__ import annotations

import json
import unittest

from deep_agent.capabilities.mcp.request_context import (
    apply_request_headers_to_stdio_config,
    serialize_request_headers,
)
from deep_agent.lib.const import REQUEST_HEADERS_ENV


class SerializeRequestHeadersTests(unittest.TestCase):
    def test_serializes_as_json_object(self) -> None:
        raw = serialize_request_headers({"Authorization": "Bearer x", "X-Tenant-Id": "t1"})
        self.assertEqual(
            json.loads(raw),
            {"Authorization": "Bearer x", "X-Tenant-Id": "t1"},
        )


class ApplyRequestHeadersToStdioConfigTests(unittest.TestCase):
    def test_injects_env_var(self) -> None:
        config = {"command": "python", "args": ["server.py"]}
        merged = apply_request_headers_to_stdio_config(
            config,
            {"authorization": "Bearer token"},
        )
        self.assertEqual(
            json.loads(merged["env"][REQUEST_HEADERS_ENV]),
            {"authorization": "Bearer token"},
        )
        self.assertIs(config.get("env"), None)

    def test_preserves_existing_env(self) -> None:
        config = {
            "command": "python",
            "args": ["server.py"],
            "env": {"FOO": "bar"},
        }
        merged = apply_request_headers_to_stdio_config(config, {"x-test": "1"})
        self.assertEqual(merged["env"]["FOO"], "bar")
        self.assertIn(REQUEST_HEADERS_ENV, merged["env"])

    def test_static_request_headers_env_wins(self) -> None:
        config = {
            "command": "python",
            "args": ["server.py"],
            "env": {REQUEST_HEADERS_ENV: '{"static": true}'},
        }
        merged = apply_request_headers_to_stdio_config(config, {"dynamic": "1"})
        self.assertEqual(merged, config)

    def test_empty_request_headers_is_noop(self) -> None:
        config = {"command": "python", "args": ["server.py"]}
        self.assertIs(apply_request_headers_to_stdio_config(config, None), config)
        self.assertIs(apply_request_headers_to_stdio_config(config, {}), config)


if __name__ == "__main__":
    unittest.main()
