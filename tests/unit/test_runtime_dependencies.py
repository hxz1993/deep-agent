from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class RuntimeDependencyTests(unittest.TestCase):
    def test_openai_sdk_is_pinned_for_platform_aiohttp(self) -> None:
        requirements = {
            line.strip()
            for line in (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertIn("openai==2.46.0", requirements)
        self.assertIn("aiohttp~=3.9.5", requirements)

    def test_pydantic_ai_openai_model_imports(self) -> None:
        from pydantic_ai.models.openai import OpenAIChatModel

        self.assertEqual(OpenAIChatModel.__name__, "OpenAIChatModel")


if __name__ == "__main__":
    unittest.main()
