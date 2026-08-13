from __future__ import annotations

import unittest
from pathlib import Path

from tests.helpers.tool_discovery_shadow import evaluate_fixture


class ToolDiscoveryShadowTests(unittest.TestCase):
    def test_sanitized_shadow_dataset_meets_first_release_gate(self) -> None:
        fixture = (
            Path(__file__).parents[1]
            / "fixtures"
            / "tool_discovery_shadow.json"
        )

        report = evaluate_fixture(fixture)

        self.assertEqual(report.case_count, 14)
        self.assertEqual(report.recall_at_5, 1.0)
        self.assertGreaterEqual(report.recall_at_3, 0.9)
        self.assertEqual(report.forbidden_at_3, 0)
        self.assertGreaterEqual(report.estimated_schema_reduction, 0.7)


if __name__ == "__main__":
    unittest.main()
