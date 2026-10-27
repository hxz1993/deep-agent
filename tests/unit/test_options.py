from __future__ import annotations

import unittest

from deep_agent.options import (
    DEFAULT_MCP_FORWARD_HEADERS,
    OPTION,
    validate_runtime_options,
)


class OptionsConvertorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = (
            OPTION.agent.mcp_forward_request_headers,
        )

    def tearDown(self) -> None:
        (
            mcp_forward_request_headers,
        ) = self._original
        OPTION.agent.mcp_forward_request_headers = mcp_forward_request_headers

    def test_forward_headers_convertor_parses_csv(self) -> None:
        OPTION.agent.mcp_forward_request_headers = "Authorization, X-Tenant-Id"
        self.assertEqual(
            OPTION.agent.mcp_forward_request_headers,
            frozenset({"authorization", "x-tenant-id"}),
        )

    def test_forward_headers_convertor_uses_default_for_blank(self) -> None:
        OPTION.agent.mcp_forward_request_headers = ""
        self.assertEqual(OPTION.agent.mcp_forward_request_headers, DEFAULT_MCP_FORWARD_HEADERS)


class ContextCompactionOptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = (
            OPTION.agent.context_compaction_trigger_ratio,
            OPTION.agent.context_summary_timeout,
        )

    def tearDown(self) -> None:
        (
            OPTION.agent.context_compaction_trigger_ratio,
            OPTION.agent.context_summary_timeout,
        ) = self._original

    def test_fixed_compaction_target_option_removed(self) -> None:
        self.assertFalse(hasattr(OPTION.agent, "context_compaction_target_ratio"))

    def test_summary_timeout_must_be_positive(self) -> None:
        OPTION.agent.context_summary_timeout = 0.0

        with self.assertRaisesRegex(ValueError, "SUMMARY_TIMEOUT"):
            validate_runtime_options()


class UploadOptionTests(unittest.TestCase):
    def test_upload_size_and_count_are_not_environment_options(self) -> None:
        self.assertFalse(hasattr(OPTION.agent, "upload_max_size_mb"))
        self.assertFalse(hasattr(OPTION.agent, "upload_max_count"))


if __name__ == "__main__":
    unittest.main()
