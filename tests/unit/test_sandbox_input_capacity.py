from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.capabilities import build_registry
from deep_agent.sandbox.framing import encode_frame
from deep_agent.sandbox.subprocess_dev.backend import SubprocessBackend
from deep_agent.sandbox.types import ExecuteRequest
from deep_agent.skills.runtime import ResolvedSkill
from deep_agent.skills.script_tool import build_skill_script_toolset
from tests.helpers.sandbox import sandbox_test_python


def _context() -> RunContext[AgentDeps]:
    return RunContext(
        deps=AgentDeps(session_id="large-sandbox-input", user="fixture-user"),
        model=TestModel(), usage=RunUsage(),
    )


class SandboxInputCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.backend = SubprocessBackend(
            python_executable=sandbox_test_python(), max_concurrency=1,
        )
        self.addAsyncCleanup(self.backend.aclose)
        self.payload = "x" * (5 * 1024 * 1024) + "完整末尾"
        self.expected = {
            "bytes": len(self.payload.encode()),
            "sha256": hashlib.sha256(self.payload.encode()).hexdigest(),
            "tail": "完整末尾",
        }

    async def test_native_python_transfers_more_than_four_mib_to_real_worker(self) -> None:
        package = build_registry(sandbox_backend=self.backend).get("builtin:sandbox")
        tool = (await package.toolset.get_tools(_context()))["execute_python"]
        code = (
            "import hashlib\n"
            "data = inputs['payload']\n"
            "{'bytes': len(data.encode()), "
            "'sha256': hashlib.sha256(data.encode()).hexdigest(), 'tail': data[-4:]}"
        )
        # The same formal request is larger than the previous IPC frame ceiling.
        self.assertGreater(len(encode_frame(ExecuteRequest(
            request_id="large-python", code=code, inputs={"payload": self.payload},
        ).model_dump(mode="json"))), 4 * 1024 * 1024)
        returned = await tool.call_func(
            {"code": code, "inputs": {"payload": self.payload}}, _context(),
        )
        self.assertEqual(returned.return_value["status"], "ok")
        self.assertEqual(returned.return_value["result"], self.expected)
        self.assertFalse(returned.return_value["truncated"]["result"])
        self.backend.execute = AsyncMock(wraps=self.backend.execute)
        with self.assertRaisesRegex(ValueError, "inputs JSON exceeds"):
            await tool.call_func(
                {"code": code, "inputs": {"payload": "x" * (16 * 1024 * 1024)}},
                _context(),
            )
        self.backend.execute.assert_not_awaited()

    async def test_native_skill_uses_shared_input_cap_and_transfers_to_real_worker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-skill-input-capacity-") as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            script = root / "scripts/check.py"
            script.write_text(
                "import hashlib, json\nfrom pathlib import Path\n"
                "data = json.loads(Path('inputs.json').read_text())['payload']\n"
                "result = {'bytes': len(data.encode()), "
                "'sha256': hashlib.sha256(data.encode()).hexdigest(), 'tail': data[-4:]}\n"
                "Path('outputs/result.json').write_text(json.dumps(result))\n",
                encoding="utf-8",
            )
            skill = ResolvedSkill(
                code="capacity", source="upload", package_root=root,
                library_root=root.parent,
                resource_index=[{"path": "scripts/check.py", "category": "script"}],
            )
            toolset = build_skill_script_toolset(
                {"capacity": skill}, backend=self.backend, timeout_sec=30,
            )
            assert toolset is not None
            tool = (await toolset.get_tools(_context()))["execute_skill_script"]
            args = {"skill_code": "capacity", "script_path": "scripts/check.py"}
            returned = await tool.call_func(
                {**args, "inputs": {"payload": self.payload}}, _context(),
            )
            self.assertEqual(returned["status"], "ok")
            self.assertEqual(returned["result"], self.expected)
            self.assertFalse(returned["truncated"]["result"])

            # Oversized input must fail at the shared request boundary, without
            # launching another worker or bypassing the installed Skill scope.
            self.backend.execute = AsyncMock(wraps=self.backend.execute)
            with self.assertRaisesRegex(ModelRetry, "inputs JSON exceeds"):
                await tool.call_func({**args, "inputs": {"payload": "x" * (16 * 1024 * 1024)}}, _context())
            self.backend.execute.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
