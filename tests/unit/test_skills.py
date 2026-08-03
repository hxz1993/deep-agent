from __future__ import annotations

import unittest

from deep_agent.capabilities.skills import load_skill_tool_packages
from deep_agent.core.paths import REPO_ROOT
from skills.calculator import calculate


class SkillLoadingTests(unittest.TestCase):
    def test_calculator_skill_is_discovered_with_tool_metadata(self) -> None:
        packages = load_skill_tool_packages(str(REPO_ROOT / "skills"))

        calculator = next(package for package in packages if package.code == "skill:calculator")
        self.assertEqual(calculator.category, "skill")
        self.assertEqual([tool.name for tool in calculator.tools], ["calculate"])

    def test_calculator_evaluates_supported_arithmetic(self) -> None:
        self.assertEqual(calculate("2 * (3 + 4) ** 2"), 98)

    def test_calculator_rejects_unsafe_expression(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported or unsafe expression"):
            calculate("__import__('os').getcwd()")


if __name__ == "__main__":
    unittest.main()
