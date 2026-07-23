from __future__ import annotations

import unittest
from pathlib import Path

from deep_agent.options import DEFAULT_MCP_FORWARD_HEADERS, OPTION


class OptionsConvertorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = (
            OPTION.agent.skills_dir,
            OPTION.agent.mcp_forward_request_headers,
        )

    def tearDown(self) -> None:
        (
            skills_dir,
            mcp_forward_request_headers,
        ) = self._original
        OPTION.agent.skills_dir = skills_dir
        OPTION.agent.mcp_forward_request_headers = mcp_forward_request_headers

    def test_skills_dir_convertor_resolves_relative_path(self) -> None:
        OPTION.agent.skills_dir = "skills"
        path = OPTION.agent.skills_dir
        self.assertTrue(path.endswith("skills") or path.endswith("skills\\"))
        self.assertTrue(Path(path).is_absolute())

    def test_forward_headers_convertor_parses_csv(self) -> None:
        OPTION.agent.mcp_forward_request_headers = "Authorization, X-Tenant-Id"
        self.assertEqual(
            OPTION.agent.mcp_forward_request_headers,
            frozenset({"authorization", "x-tenant-id"}),
        )

    def test_forward_headers_convertor_uses_default_for_blank(self) -> None:
        OPTION.agent.mcp_forward_request_headers = ""
        self.assertEqual(OPTION.agent.mcp_forward_request_headers, DEFAULT_MCP_FORWARD_HEADERS)


if __name__ == "__main__":
    unittest.main()
