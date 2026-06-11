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


if __name__ == "__main__":
    unittest.main()
