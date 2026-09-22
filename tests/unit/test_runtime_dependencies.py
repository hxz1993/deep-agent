from __future__ import annotations

import unittest
from importlib.metadata import version
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class RuntimeDependencyTests(unittest.TestCase):
    def test_openai_sdk_and_platform_aiohttp_are_jointly_upgraded(self) -> None:
        requirements = {
            line.strip()
            for line in (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertIn("openai==3.8.0", requirements)
        self.assertIn("aiohttp>=3.14.3,<4", requirements)
        self.assertIn("deepfos>=1.1.97,<2", requirements)
        self.assertIn("httpx2>=2.7,<3", requirements)

    def test_pydantic_ai_and_harness_are_jointly_pinned(self) -> None:
        requirements = {
            line.strip()
            for line in (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertIn(
            "pydantic-ai-slim[openai,anthropic,google,mcp,duckduckgo,tavily,web-fetch]==2.46.0",
            requirements,
        )
        self.assertIn(
            "pydantic-ai-harness[skills,exa,youdotcom]==0.32.0",
            requirements,
        )
        self.assertIn("pydantic-monty==0.0.23", requirements)
        self.assertEqual(version("pydantic-ai-slim"), "2.46.0")
        self.assertEqual(version("pydantic-ai-harness"), "0.32.0")
        self.assertEqual(version("pydantic-monty"), "0.0.23")
        self.assertEqual(version("openai"), "3.8.0")
        self.assertGreaterEqual(tuple(map(int, version("aiohttp").split(".")[:2])), (3, 14))

    def test_pydantic_ai_openai_model_imports(self) -> None:
        from pydantic_ai.models.openai import OpenAIChatModel

        self.assertEqual(OpenAIChatModel.__name__, "OpenAIChatModel")

    def test_code_mode_public_runtime_contract(self) -> None:
        from pydantic_ai_harness import CodeMode
        from pydantic_ai_harness.code_mode import CodeModeToolset
        from pydantic_monty import Monty

        self.assertEqual(CodeMode.__name__, "CodeMode")
        self.assertEqual(CodeModeToolset.__name__, "CodeModeToolset")
        self.assertEqual(Monty.__name__, "Monty")

    def test_deepcube_uses_pipeline_injected_internal_wheel(self) -> None:
        requirements = {
            line.strip()
            for line in (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertIn("deepcube[mcp]", requirements)
        self.assertFalse(any(line.startswith("deepcube[mcp]==") for line in requirements))


if __name__ == "__main__":
    unittest.main()
