from __future__ import annotations

import unittest

from fastapi import HTTPException
from starlette.requests import Request

from deep_agent.agent.headers import extract_forward_headers
from deep_agent.options import DEFAULT_MCP_FORWARD_HEADERS, OPTION


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
        request = _request(
            [
                (b"authorization", b"Bearer secret"),
                (b"x-tenant-id", b"t1"),
                (b"x-custom", b"ignored"),
            ]
        )
        self.assertEqual(
            extract_forward_headers(
                request,
                allowed=frozenset({"authorization", "x-tenant-id"}),
            ),
            {
                "authorization": "Bearer secret",
                "x-tenant-id": "t1",
            },
        )

    def test_default_allowlist_when_empty(self) -> None:
        OPTION.agent.mcp_forward_request_headers = ""
        self.assertIn("authorization", OPTION.agent.mcp_forward_request_headers)
        self.assertIn("app", OPTION.agent.mcp_forward_request_headers)
        self.assertIn("language", OPTION.agent.mcp_forward_request_headers)
        self.assertIn("x-deepfos-*", OPTION.agent.mcp_forward_request_headers)

    def test_extract_forward_headers_supports_wildcard_prefix(self) -> None:
        request = _request(
            [
                (b"X-DeepFOS-Token", b"abc"),
                (b"x-deepfos-space", b"s1"),
                (b"x-deepf", b"ignored"),
            ]
        )
        self.assertEqual(
            extract_forward_headers(request, allowed=frozenset({"x-deepfos-*"})),
            {
                "X-DeepFOS-Token": "abc",
                "x-deepfos-space": "s1",
            },
        )

    def test_extract_forward_headers_is_case_insensitive(self) -> None:
        request = _request(
            [
                (b"app", b"demo001"),
                (b"LANGUAGE", b"zh-CN"),
                (b"cookie", b"ignored"),
            ]
        )
        self.assertEqual(
            extract_forward_headers(request, allowed=frozenset({"app", "language"})),
            {
                "app": "demo001",
                "language": "zh-CN",
            },
        )

    def test_app_only_context_derives_space_and_defaults_language(self) -> None:
        request = _request([(b"app", b"mdxath003")])

        self.assertEqual(
            extract_forward_headers(request),
            {
                "app": "mdxath003",
                "space": "mdxath",
                "language": "zh-cn",
            },
        )

    def test_console_context_avoids_gateway_header_and_forwards_canonical_values(self) -> None:
        request = _request(
            [
                (b"x-deep-agent-app", b"iirjgi005"),
                (b"x-deep-agent-language", b"zh-cn"),
            ]
        )

        self.assertEqual(
            extract_forward_headers(request),
            {
                "app": "iirjgi005",
                "space": "iirjgi",
                "language": "zh-cn",
            },
        )

    def test_console_app_override_ignores_inherited_platform_space(self) -> None:
        request = _request(
            [
                (b"app", b"platform001"),
                (b"space", b"platform"),
                (b"x-deep-agent-app", b"iirjgi005"),
            ]
        )

        self.assertEqual(
            extract_forward_headers(request),
            {
                "app": "iirjgi005",
                "space": "iirjgi",
                "language": "zh-cn",
            },
        )

    def test_matching_explicit_context_and_language_remain_supported(self) -> None:
        request = _request(
            [
                (b"app", b"mdxath003"),
                (b"space", b"mdxath"),
                (b"language", b"en"),
            ]
        )

        self.assertEqual(
            extract_forward_headers(request),
            {
                "app": "mdxath003",
                "space": "mdxath",
                "language": "en",
            },
        )

    def test_mismatched_space_is_rejected(self) -> None:
        request = _request(
            [
                (b"app", b"mdxath003"),
                (b"space", b"other"),
            ]
        )

        with self.assertRaises(HTTPException) as caught:
            extract_forward_headers(request)

        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("mdxath", str(caught.exception.detail))

    def test_invalid_app_is_rejected(self) -> None:
        request = _request([(b"app", b"mdxath")])

        with self.assertRaises(HTTPException) as caught:
            extract_forward_headers(request)

        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("三位数字", str(caught.exception.detail))

    def test_default_mcp_forward_headers_constant(self) -> None:
        self.assertEqual(OPTION.agent.mcp_forward_request_headers, DEFAULT_MCP_FORWARD_HEADERS)


if __name__ == "__main__":
    unittest.main()
