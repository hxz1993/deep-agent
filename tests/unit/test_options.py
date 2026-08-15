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
            OPTION.agent.context_compaction_target_ratio,
            OPTION.agent.context_summary_timeout,
        )

    def tearDown(self) -> None:
        (
            OPTION.agent.context_compaction_trigger_ratio,
            OPTION.agent.context_compaction_target_ratio,
            OPTION.agent.context_summary_timeout,
        ) = self._original

    def test_target_must_be_lower_than_trigger(self) -> None:
        OPTION.agent.context_compaction_trigger_ratio = 0.60
        OPTION.agent.context_compaction_target_ratio = 0.60

        with self.assertRaisesRegex(ValueError, "TARGET_RATIO"):
            validate_runtime_options()

    def test_summary_timeout_must_be_positive(self) -> None:
        OPTION.agent.context_summary_timeout = 0.0

        with self.assertRaisesRegex(ValueError, "SUMMARY_TIMEOUT"):
            validate_runtime_options()


class UploadOptionTests(unittest.TestCase):
    def test_upload_limits_are_positive(self) -> None:
        with self.assertRaisesRegex(Exception, "positive integer"):
            OPTION.agent.upload_max_size_mb = 0
        with self.assertRaisesRegex(Exception, "positive integer"):
            OPTION.agent.upload_max_count = -1


if __name__ == "__main__":
    unittest.main()
