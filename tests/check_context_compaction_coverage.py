"""Run the context compaction regression suite and enforce focused coverage gates."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from coverage import Coverage


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
for import_root in (REPOSITORY_ROOT, REPOSITORY_ROOT / "src"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))


TARGET = "src/deep_agent/agent/context/compaction.py"
REPORT_PATH = Path("/tmp/deep-agent-context-compaction-coverage-TMP_to_delete.json")
TEST_MODULES = (
    "tests.unit.test_context_compaction",
    "tests.unit.test_context_references",
    "tests.unit.test_context_test_scenarios",
    "tests.integration.test_session_model_override",
)
MIN_LINE_PERCENT = 95.0
MIN_BRANCH_PERCENT = 85.0


def _percentage(covered: int, total: int) -> float:
    return 100.0 if total == 0 else covered * 100.0 / total


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="deep-agent-context-coverage-TMP_to_delete-"
    ) as temporary:
        temporary_path = Path(temporary)
        coverage = Coverage(
            branch=True,
            source=["deep_agent"],
            data_file=str(temporary_path / ".coverage"),
        )
        coverage.start()
        suite = unittest.defaultTestLoader.loadTestsFromNames(TEST_MODULES)
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        coverage.stop()
        coverage.save()
        if not result.wasSuccessful():
            return 1

        coverage.json_report(outfile=str(REPORT_PATH), include=TARGET)
        report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        summary = report["files"][TARGET]["summary"]
        line_percent = _percentage(
            summary["covered_lines"],
            summary["num_statements"],
        )
        branch_percent = _percentage(
            summary["covered_branches"],
            summary["num_branches"],
        )
        print(
            f"{TARGET}: line={line_percent:.2f}% "
            f"branch={branch_percent:.2f}% "
            f"({summary['covered_lines']}/{summary['num_statements']} lines, "
            f"{summary['covered_branches']}/{summary['num_branches']} branches)"
        )
        print(f"coverage report: {REPORT_PATH}")
        if line_percent < MIN_LINE_PERCENT or branch_percent < MIN_BRANCH_PERCENT:
            print(
                "coverage gate failed: "
                f"required line>={MIN_LINE_PERCENT:.0f}% "
                f"and branch>={MIN_BRANCH_PERCENT:.0f}%",
                file=sys.stderr,
            )
            return 2
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
