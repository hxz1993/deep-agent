from __future__ import annotations

import json
import unittest

from deep_agent.sandbox.backend import PythonExecutionBackend
from deep_agent.sandbox.types import (
    DEFAULT_TIMEOUT_MS,
    ExecuteRequest,
    ExecuteResult,
    MAX_CODE_BYTES,
    MAX_INPUTS_JSON_BYTES,
    MAX_SCRIPT_RESOURCE_BYTES,
    MAX_STDOUT_BYTES,
    SandboxInputFile,
    SandboxError,
    TruncationMetadata,
)


class SandboxTypesTests(unittest.TestCase):
    def test_expression_and_script_accept_sixteen_mib_json_with_utf8_boundary(self) -> None:
        # The cap applies to the serialized UTF-8 object, including JSON syntax.
        cap = 16 * 1024 * 1024
        overhead = len(json.dumps({"payload": ""}, separators=(",", ":")).encode())
        value = "汉" + "x" * (cap - overhead - len("汉".encode()))
        for mode in ("expression", "script"):
            with self.subTest(mode=mode):
                script_fields = ({"script_entry": "scripts/check.py", "skill_files": (
                    SandboxInputFile.from_bytes("scripts/check.py", b"pass"),)} if mode == "script" else {})
                request = ExecuteRequest(
                    request_id=f"large-{mode}", mode=mode, code="" if mode == "script" else "pass",
                    inputs={"payload": value}, **script_fields,
                )
                self.assertEqual(request.inputs["payload"], value)
                with self.assertRaisesRegex(ValueError, "inputs JSON exceeds"):
                    ExecuteRequest(
                        request_id=f"oversized-{mode}", mode=mode, code="" if mode == "script" else "pass",
                        inputs={"payload": value + "x"}, **script_fields,
                    )

    def test_execute_request_rejects_oversized_code(self) -> None:
        with self.assertRaises(ValueError):
            ExecuteRequest(
                request_id="call-1",
                code="x" * (MAX_CODE_BYTES + 1),
                inputs={},
                timeout_ms=30_000,
            )

    def test_execute_request_rejects_oversized_inputs(self) -> None:
        oversized = {"payload": "y" * MAX_INPUTS_JSON_BYTES}
        with self.assertRaises(ValueError):
            ExecuteRequest(
                request_id="call-1",
                code="pass",
                inputs=oversized,
                timeout_ms=30_000,
            )

    def test_execute_request_default_timeout(self) -> None:
        request = ExecuteRequest(
            request_id="call-1",
            code="pass",
            inputs={},
        )
        self.assertEqual(request.timeout_ms, DEFAULT_TIMEOUT_MS)
        self.assertEqual(DEFAULT_TIMEOUT_MS, 30_000)

    def test_script_request_accepts_only_controlled_relative_files(self) -> None:
        request = ExecuteRequest(
            request_id="script-1",
            mode="script",
            code="",
            script_entry="scripts/check.py",
            skill_files=(SandboxInputFile.from_bytes("scripts/check.py", b"pass"),),
            arguments=("--input", "inputs.json"),
            files=(SandboxInputFile.from_bytes("assets/input.txt", b"ok"),),
        )
        self.assertEqual(request.files[0].decoded(), b"ok")
        with self.assertRaises(ValueError):
            SandboxInputFile.from_bytes("../secret", b"bad")
        with self.assertRaises(ValueError):
            SandboxInputFile.from_bytes("outputs/result.json", b"bad")

    def test_expression_mode_accepts_authorized_files(self) -> None:
        request = ExecuteRequest(
            request_id="expression-files",
            code="pass",
            files=(
                SandboxInputFile.from_bytes(
                    "assets/input.txt",
                    b"ok",
                    file_id="file-1",
                ),
            ),
        )
        self.assertEqual(request.files[0].file_id, "file-1")

    def test_script_request_rejects_oversized_resource_payload(self) -> None:
        with self.assertRaises(ValueError):
            ExecuteRequest(
                request_id="script-files",
                mode="script",
                code="",
                script_entry="scripts/check.py",
                skill_files=(SandboxInputFile.from_bytes("scripts/check.py", b"pass"),),
                files=(
                    SandboxInputFile.from_bytes(
                        "assets/input.bin",
                        b"x" * (MAX_SCRIPT_RESOURCE_BYTES + 1),
                    ),
                ),
            )

    def test_execute_result_rejects_oversized_stdout(self) -> None:
        with self.assertRaises(ValueError):
            ExecuteResult(
                request_id="call-1",
                status="ok",
                exit_code=0,
                stdout="o" * (MAX_STDOUT_BYTES + 1),
                stderr="",
                result=None,
                error=None,
                duration_ms=1,
                backend="subprocess-dev",
                environment="test",
                security_level="none",
                memory_peak_bytes=None,
                truncated=TruncationMetadata(),
            )

    def test_sandbox_error_rejects_oversized_traceback(self) -> None:
        from deep_agent.sandbox.types import MAX_TRACEBACK_BYTES

        with self.assertRaises(ValueError):
            SandboxError(
                code="PYTHON_EXCEPTION",
                message="boom",
                traceback="t" * (MAX_TRACEBACK_BYTES + 1),
            )

    def test_models_are_frozen(self) -> None:
        request = ExecuteRequest(
            request_id="call-1",
            code="pass",
            inputs={},
        )
        with self.assertRaises(Exception):
            request.code = "mutated"

    def test_backend_protocol_has_no_runtime_lifecycle(self) -> None:
        self.assertFalse(hasattr(PythonExecutionBackend, "reset"))
        self.assertFalse(hasattr(PythonExecutionBackend, "destroy"))


if __name__ == "__main__":
    unittest.main()
