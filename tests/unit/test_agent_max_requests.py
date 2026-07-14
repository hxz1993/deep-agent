from __future__ import annotations

import unittest

from pydantic_ai.exceptions import UsageLimitExceeded

from deep_agent.agent.limits import (
    default_agent_max_requests,
    format_stream_error,
    resolve_agent_max_requests,
    validate_agent_max_requests,
)


class AgentMaxRequestsTests(unittest.TestCase):
    def test_default_is_half_of_global_max(self) -> None:
        self.assertEqual(default_agent_max_requests(50), 25)
        self.assertEqual(default_agent_max_requests(51), 25)

    def test_resolve_uses_default_when_unset(self) -> None:
        self.assertEqual(resolve_agent_max_requests(None, 50), 25)

    def test_resolve_caps_configured_value(self) -> None:
        self.assertEqual(resolve_agent_max_requests(40, 50), 40)
        self.assertEqual(resolve_agent_max_requests(60, 50), 50)

    def test_validate_rejects_out_of_range(self) -> None:
        with self.assertRaises(ValueError):
            validate_agent_max_requests(0, 50)
        with self.assertRaises(ValueError):
            validate_agent_max_requests(51, 50)

    def test_validate_accepts_valid_values(self) -> None:
        validate_agent_max_requests(None, 50)
        validate_agent_max_requests(1, 50)
        validate_agent_max_requests(50, 50)

    def test_format_stream_error_request_limit(self) -> None:
        exc = UsageLimitExceeded("The next request would exceed the request_limit of 30")
        self.assertEqual(
            format_stream_error(exc),
            "本轮对话模型请求次数已达上限（30 次），请简化问题或开启新对话后重试。",
        )

    def test_format_stream_error_total_tokens_limit(self) -> None:
        exc = UsageLimitExceeded(
            "The next request would exceed the total_tokens_limit of 1000000 (total_tokens=1000001)"
        )
        self.assertEqual(
            format_stream_error(exc),
            "本轮对话 token 用量已达上限（1000000），请缩短上下文或开启新对话后重试。",
        )

    def test_format_stream_error_passthrough(self) -> None:
        self.assertEqual(format_stream_error(RuntimeError("boom")), "boom")


if __name__ == "__main__":
    unittest.main()
