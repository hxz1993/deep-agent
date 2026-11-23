"""The shipped Console theme follows the bundled chart Skill, never a second palette."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("sync_console_g2_theme", ROOT / "scripts/sync_console_g2_theme.py")
assert spec is not None and spec.loader is not None
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ConsoleG2ThemeTests(unittest.TestCase):
    def test_generated_theme_matches_current_bundled_skill(self):
        actual = (ROOT / "console/g2-theme.js").read_text("utf-8")
        expected = builder.render_theme()
        self.assertEqual(actual, expected)
        self.assertEqual(expected, builder.render_theme())


if __name__ == "__main__":
    unittest.main()
