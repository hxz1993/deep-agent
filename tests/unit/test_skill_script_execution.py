from __future__ import annotations

import asyncio
import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from pydantic import ValidationError
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.spec import AgentSpec
from deep_agent.api.v1.deps import _skill_script_execution_available
from deep_agent.sandbox.subprocess_dev.backend import SubprocessBackend
from deep_agent.sandbox.types import (
    BackendHealth,
    ExecuteRequest,
    ExecuteResult,
    MAX_INPUTS_JSON_BYTES,
    SandboxInputFile,
    TruncationMetadata,
)
from deep_agent.skills.runtime import ResolvedSkill, ResolvedSkills
from deep_agent.skills.script_tool import build_skill_script_toolset
from tests.helpers.sandbox import sandbox_test_python


class RecordingBackend:
    def __init__(self) -> None:
        self.requests: list[ExecuteRequest] = []
        self.health = AsyncMock(
            return_value=BackendHealth(
                enabled=True,
                backend="test",
                state_scope="request",
                security_level="none",
            )
        )
        self.aclose = AsyncMock()

    async def execute(self, request: ExecuteRequest) -> ExecuteResult:
        self.requests.append(request)
        return ExecuteResult(
            request_id=request.request_id,
            status="ok",
            exit_code=0,
            stdout="validated\n",
            stderr="",
            result={"valid": True},
            error=None,
            duration_ms=1,
            backend="test",
            environment="test",
            security_level="none",
            memory_peak_bytes=None,
            truncated=TruncationMetadata(),
        )


def _context(*, user: str | None = "user") -> RunContext[AgentDeps]:
    return RunContext(
        deps=AgentDeps(session_id="skill-script", user=user),
        model=TestModel(),
        usage=RunUsage(),
    )


class SkillScriptValidationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="TMP_to_delete-skill-validation-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "scripts").mkdir()
        (root / "scripts/check.py").write_text("pass", encoding="utf-8")
        item = ResolvedSkill(
            code="demo", source="upload", package_root=root, library_root=root.parent,
            resource_index=[{"path": "scripts/check.py", "category": "script"}],
        )
        self.backend = RecordingBackend()
        self.toolset = build_skill_script_toolset({"demo": item}, backend=self.backend, timeout_sec=5)
        assert self.toolset is not None

    def _safe_errors(self, message: str, *, field: str, marker: str) -> None:
        self.assertNotIn(marker, message)
        self.assertNotIn("'input':", message)
        self.assertNotIn("'ctx':", message)
        self.assertNotIn("'url':", message)
        self.assertNotIn("https://errors.pydantic.dev", message)
        prefix = "Invalid Skill script execution request: "
        self.assertTrue(message.startswith(prefix))
        errors = ast.literal_eval(message[len(prefix):])
        self.assertEqual(len(errors), 1)
        self.assertEqual(set(errors[0]), {"type", "loc", "msg"})
        self.assertEqual(errors[0]["type"], "value_error")
        self.assertEqual(errors[0]["loc"], (field,))
        self.assertTrue(errors[0]["msg"].startswith("Value error, "))

    async def test_oversized_inputs_and_arguments_retry_without_echoing_private_values(self) -> None:
        marker = "SECRET123"
        tool = (await self.toolset.get_tools(_context()))["execute_skill_script"]
        self.assertEqual(MAX_INPUTS_JSON_BYTES, 16 * 1024 * 1024)
        for field, params in (
            ("inputs", {"inputs": {"x": marker + "x" * MAX_INPUTS_JSON_BYTES + marker}}),
            ("arguments", {"arguments": [marker * 1_000]}),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ModelRetry) as caught:
                    await tool.call_func({"skill_code": "demo", "script_path": "scripts/check.py", **params}, _context())
                self.assertEqual(self.backend.requests, [])
                self.assertIsInstance(caught.exception.__cause__, ValidationError)
                self._safe_errors(str(caught.exception), field=field, marker=marker)
                expected = (f"inputs JSON exceeds {MAX_INPUTS_JSON_BYTES} bytes" if field == "inputs"
                            else "script argument is invalid or too large")
                self.assertIn(expected, str(caught.exception))

    async def test_sdk_retry_is_safe_while_original_call_and_corrected_input_are_preserved(self) -> None:
        marker = "SECRET123"
        bad_args = {"skill_code": "demo", "script_path": "scripts/check.py", "arguments": [marker * 1_000]}
        corrected = {"skill_code": "demo", "script_path": "scripts/check.py", "arguments": ["--check"],
                     "inputs": {"customer": marker, "values": [0, False, None, "甲\\乙\n"]}}
        step = 0

        def respond(messages, info):
            nonlocal step
            step += 1
            self.assertEqual(set(info.function_tools[0].parameters_json_schema["properties"]),
                             {"skill_code", "script_path", "arguments", "inputs", "resource_paths"})
            if step == 1:
                return ModelResponse(parts=[ToolCallPart("execute_skill_script", bad_args, "invalid")])
            if step == 2:
                self.assertEqual(self.backend.requests, [])
                retries = [part for message in messages for part in message.parts if isinstance(part, RetryPromptPart)]
                self.assertEqual(len(retries), 1)
                self.assertEqual(retries[0].tool_call_id, "invalid")
                self._safe_errors(retries[0].content, field="arguments", marker=marker)
                original = next(part for message in messages for part in message.parts
                                if isinstance(part, ToolCallPart) and part.tool_call_id == "invalid")
                self.assertEqual(original.args_as_dict(), bad_args)
                return ModelResponse(parts=[ToolCallPart("execute_skill_script", corrected, "valid")])
            self.assertEqual(len(self.backend.requests), 1)
            return ModelResponse(parts=[TextPart("done")])

        agent = Agent(FunctionModel(respond), deps_type=AgentDeps, toolsets=[self.toolset])
        result = await agent.run("Validate and correct the synthetic request.", deps=_context().deps)
        self.assertEqual(result.output, "done")
        self.assertEqual(step, 3)
        self.assertEqual(self.backend.requests[0].inputs, corrected["inputs"])
        self.assertEqual(self.backend.requests[0].arguments, ("--check",))
        original = next(part for message in result.new_messages() for part in message.parts
                        if isinstance(part, ToolCallPart) and part.tool_call_id == "invalid")
        self.assertEqual(original.args_as_dict(), bad_args)


class SkillScriptProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_disclosure_requires_selected_sandbox_and_script(self) -> None:
        item = ResolvedSkill(
            code="demo",
            source="upload",
            package_root=Path("/tmp/demo"),
            library_root=Path("/tmp"),
            resource_index=[{"path": "scripts/a.py", "category": "script"}],
        )
        resolved = ResolvedSkills(
            configured=("demo",),
            items=(item,),
            unavailable=(),
            capability=None,
        )
        package = MagicMock(code="builtin:sandbox")
        ctx = MagicMock()
        ctx.registry.selected.return_value = [package]
        ctx.sandbox_backend = MagicMock()
        spec = AgentSpec(name="demo", model="test", instructions="", capabilities=["builtin:sandbox"])
        self.assertTrue(_skill_script_execution_available(spec, ctx, resolved))

        ctx.registry.selected.return_value = []
        self.assertFalse(_skill_script_execution_available(spec, ctx, resolved))
        ctx.registry.selected.return_value = [package]
        self.assertFalse(
            _skill_script_execution_available(
                spec,
                ctx,
                ResolvedSkills(configured=(), items=(), unavailable=(), capability=None),
            )
        )

    async def test_subprocess_dev_supports_internal_script_protocol(self) -> None:
        backend = SubprocessBackend(
            python_executable=sandbox_test_python(),
            max_concurrency=1,
        )
        try:
            result = await backend.execute(
                ExecuteRequest(
                    request_id="script-protocol",
                    mode="script",
                    code=(
                        "import json\n"
                        "from pathlib import Path\n"
                        "values = json.loads(Path('inputs.json').read_text())['values']\n"
                        "label = Path('assets/label.txt').read_text()\n"
                        "Path('outputs/result.json').write_text("
                        "json.dumps({'label': label, 'total': sum(values)}))\n"
                        "print('validated')\n"
                    ),
                    inputs={"values": [1, 2, 3]},
                    files=(SandboxInputFile.from_bytes("assets/label.txt", b"demo"),),
                )
            )
        finally:
            await backend.aclose()
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.result, {"label": "demo", "total": 6})
        self.assertEqual(result.stdout.strip(), "validated")
        self.assertEqual(result.exit_code, 0)

    async def test_nonzero_script_keeps_fixed_result_for_diagnostics(self) -> None:
        backend = SubprocessBackend(
            python_executable=sandbox_test_python(),
            max_concurrency=1,
        )
        try:
            result = await backend.execute(
                ExecuteRequest(
                    request_id="script-invalid",
                    mode="script",
                    code=(
                        "import json\n"
                        "from pathlib import Path\n"
                        "Path('outputs/result.json').write_text("
                        "json.dumps({'valid': False, 'errors': ['bad']}))\n"
                        "raise SystemExit(1)\n"
                    ),
                )
            )
        finally:
            await backend.aclose()
        self.assertEqual(result.status, "error")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.result, {"valid": False, "errors": ["bad"]})

    async def test_cancelled_script_propagates_cancellation(self) -> None:
        backend = SubprocessBackend(
            python_executable=sandbox_test_python(),
            max_concurrency=1,
        )
        task = asyncio.create_task(
            backend.execute(
                ExecuteRequest(
                    request_id="script-cancel",
                    mode="script",
                    code="import time\ntime.sleep(30)\n",
                )
            )
        )
        try:
            await asyncio.sleep(0.1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            await backend.aclose()

    async def test_model_tool_builds_controlled_script_request(self) -> None:
        backend = RecordingBackend()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "scripts").mkdir()
            (root / "assets").mkdir()
            (root / "scripts/check.py").write_text("print('ok')", encoding="utf-8")
            (root / "assets/input.json").write_text('{"x":1}', encoding="utf-8")
            item = ResolvedSkill(
                code="demo",
                source="upload",
                package_root=root,
                library_root=root.parent,
                resource_index=[
                    {
                        "path": "scripts/check.py",
                        "category": "script",
                        "mime": "text/x-python",
                        "size": 11,
                        "content_hash": "script",
                        "readable": False,
                    },
                    {
                        "path": "assets/input.json",
                        "category": "asset",
                        "mime": "application/json",
                        "size": 7,
                        "content_hash": "asset",
                        "readable": True,
                    },
                ],
            )
            toolset = build_skill_script_toolset(
                {"demo": item}, backend=backend, timeout_sec=12
            )
            assert toolset is not None
            tools = await toolset.get_tools(_context())
            response = await tools["execute_skill_script"].call_func(
                {
                    "skill_code": "demo",
                    "script_path": "scripts/check.py",
                    "arguments": ["--input", "inputs.json"],
                    "inputs": {"value": 3},
                    "resource_paths": ["assets/input.json"],
                },
                _context(),
            )

        self.assertEqual(response["result"], {"valid": True})
        self.assertEqual(len(backend.requests), 1)
        request = backend.requests[0]
        self.assertEqual(request.mode, "script")
        self.assertEqual(request.arguments, ("--input", "inputs.json"))
        self.assertEqual(request.inputs, {"value": 3})
        self.assertEqual(request.files[0].path, "assets/input.json")
        self.assertEqual(request.timeout_ms, 12_000)

    async def test_cross_skill_and_script_as_resource_are_rejected(self) -> None:
        backend = RecordingBackend()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "scripts").mkdir()
            (root / "scripts/check.py").write_text("pass", encoding="utf-8")
            item = ResolvedSkill(
                code="demo",
                source="upload",
                package_root=root,
                library_root=root.parent,
                resource_index=[
                    {
                        "path": "scripts/check.py",
                        "category": "script",
                        "mime": "text/x-python",
                        "size": 4,
                        "content_hash": "script",
                        "readable": False,
                    }
                ],
            )
            toolset = build_skill_script_toolset(
                {"demo": item}, backend=backend, timeout_sec=5
            )
            assert toolset is not None
            tools = await toolset.get_tools(_context())
            with self.assertRaises(ModelRetry):
                await tools["execute_skill_script"].call_func(
                    {
                        "skill_code": "other",
                        "script_path": "scripts/check.py",
                    },
                    _context(),
                )
            with self.assertRaises(ModelRetry):
                await tools["execute_skill_script"].call_func(
                    {
                        "skill_code": "demo",
                        "script_path": "scripts/check.py",
                        "resource_paths": ["scripts/check.py"],
                    },
                    _context(),
                )


if __name__ == "__main__":
    unittest.main()
