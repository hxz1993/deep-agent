from __future__ import annotations

import unittest

from starlette.requests import Request

from deep_agent.agent.headers import extract_forward_headers, forward_header_names
from deep_agent.core.config import Settings


def _request(headers: list[tuple[bytes, bytes]]) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/chat",
        "headers": headers,
    }
    return Request(scope)


class ForwardHeaderAllowlistTests(unittest.TestCase):
    def test_extract_forward_headers_uses_allowlist(self) -> None:
        settings = Settings(mcp_forward_request_headers="authorization,x-tenant-id")
        request = _request(
            [
                (b"authorization", b"Bearer secret"),
                (b"x-tenant-id", b"t1"),
                (b"x-custom", b"ignored"),
            ]
        )
        self.assertEqual(
            extract_forward_headers(request, settings),
            {
                "authorization": "Bearer secret",
                "x-tenant-id": "t1",
            },
        )

    def test_forward_header_names_defaults_when_empty(self) -> None:
        settings = Settings(mcp_forward_request_headers="")
        self.assertIn("authorization", forward_header_names(settings))
        self.assertIn("app", forward_header_names(settings))
        self.assertIn("language", forward_header_names(settings))
        self.assertIn("x-deepfos-*", forward_header_names(settings))

    def test_extract_forward_headers_supports_wildcard_prefix(self) -> None:
        settings = Settings(mcp_forward_request_headers="x-deepfos-*")
        request = _request(
            [
                (b"X-DeepFOS-Token", b"abc"),
                (b"x-deepfos-space", b"s1"),
                (b"x-deepf", b"ignored"),
            ]
        )
        self.assertEqual(
            extract_forward_headers(request, settings),
            {
                "X-DeepFOS-Token": "abc",
                "x-deepfos-space": "s1",
            },
        )

    def test_extract_forward_headers_is_case_insensitive(self) -> None:
        settings = Settings(mcp_forward_request_headers="APP,Language")
        request = _request(
            [
                (b"app", b"demo"),
                (b"LANGUAGE", b"zh-CN"),
                (b"cookie", b"ignored"),
            ]
        )
        self.assertEqual(
            extract_forward_headers(request, settings),
            {
                "app": "demo",
                "LANGUAGE": "zh-CN",
            },
        )


if __name__ == "__main__":
    unittest.main()
