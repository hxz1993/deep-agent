from __future__ import annotations

import unittest

from deep_agent.agent.limits import (
    default_agent_max_requests,
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


if __name__ == "__main__":
    unittest.main()
