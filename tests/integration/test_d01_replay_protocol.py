"""No-network acceptance for the D01 Runner evaluation apparatus."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "tests/integration/helpers/d01_replay.py"


class D01ReplayProtocolTests(unittest.TestCase):
    def test_fresh_holdout_keeps_tuning_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-d01-freeze-", dir=ROOT) as directory:
            first, fresh = Path(directory) / "first", Path(directory) / "fresh"
            for path, flags in [(first, []), (fresh, ["--fresh-holdout"])]:
                result = subprocess.run([sys.executable, str(DRIVER), "--freeze", "--dataset", str(path), *flags],
                                        cwd=ROOT, text=True, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr[-1000:])
            for index in range(1, 9):
                relative = f"inputs/D{index:02d}.json"
                equal = (first / relative).read_bytes() == (fresh / relative).read_bytes()
                self.assertEqual(equal, index <= 4)
            manifest = (fresh / "manifest.json").read_bytes()
            repeat = subprocess.run([sys.executable, str(DRIVER), "--freeze", "--dataset", str(fresh)],
                                    cwd=ROOT, text=True, capture_output=True, timeout=30)
            self.assertNotEqual(repeat.returncode, 0)
            self.assertEqual((fresh / "manifest.json").read_bytes(), manifest)

    def test_runner_replays_two_compactions_then_reads_and_applies(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-d01-protocol-", dir=ROOT) as directory:
            base = Path(directory)
            dataset = base / "sealed"
            output = base / "run"
            common = [sys.executable, str(DRIVER), "--dataset", str(dataset)]
            frozen = subprocess.run(common + ["--freeze"], cwd=ROOT, text=True,
                                    capture_output=True, timeout=30)
            self.assertEqual(frozen.returncode, 0, frozen.stderr[-2000:])
            run = subprocess.run(common + ["--model", "fixture", "--cases", "D01", "--repeats", "1",
                                           "--source-root", str(ROOT), "--output", str(output)],
                                 cwd=ROOT, text=True, capture_output=True, timeout=90)
            self.assertEqual(run.returncode, 0, run.stderr[-2000:])
            metrics = json.loads((output / "metrics.json").read_text())[0]
            self.assertEqual(metrics["status"], "completed")
            self.assertEqual(metrics["field_correct"], 6)
            self.assertTrue(metrics["permission"])
            self.assertTrue(metrics["no_duplicate_write"])
            self.assertGreaterEqual(metrics["summary_count"], 2)
            self.assertEqual(metrics["compaction_statuses"], {"completed": 2})
            self.assertEqual(metrics["tool_counts"]["workbook_apply"], 1)
            self.assertGreaterEqual(metrics["tool_counts"]["evidence_read"], 1)
            self.assertGreaterEqual(metrics["tool_counts"]["workbook_read"], 1)
            self.assertTrue((output / "D01-r1/stage-1.sse.synthetic.json").exists())
            self.assertTrue((output / "D01-r1/TMP_to_delete-replay.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
