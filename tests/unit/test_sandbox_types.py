from __future__ import annotations

import unittest

from deep_agent.sandbox.backend import PythonExecutionBackend
from deep_agent.sandbox.types import (
    DEFAULT_TIMEOUT_MS,
    ExecuteRequest,
    ExecuteResult,
    MAX_CODE_BYTES,
    MAX_INPUTS_JSON_BYTES,
    MAX_STDOUT_BYTES,
    SandboxError,
    TruncationMetadata,
)


class SandboxTypesTests(unittest.TestCase):
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
