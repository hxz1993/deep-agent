from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import unittest

from deep_agent.sandbox.framing import encode_frame, read_frame
from deep_agent.sandbox.types import (
    MAX_RESULT_BYTES,
    MAX_STDERR_BYTES,
    MAX_STDOUT_BYTES,
    MAX_TRACEBACK_BYTES,
)

_PANDAS_AVAILABLE = importlib.util.find_spec("pandas") is not None


def _run_worker(
    code: str,
    *,
    inputs: dict | None = None,
    request_id: str = "test-1",
    timeout: float = 10.0,
) -> dict:
    request = {
        "request_id": request_id,
        "code": code,
        "inputs": inputs or {},
    }
    completed = subprocess.run(
        [sys.executable, "-m", "deep_agent.sandbox.worker"],
        input=encode_frame(request),
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"worker exited {completed.returncode}; "
            f"stderr={completed.stderr!r}; stdout={completed.stdout!r}"
        )
    if completed.stderr:
        raise AssertionError(f"worker wrote to stderr: {completed.stderr!r}")
    return read_frame(io.BytesIO(completed.stdout))


class SandboxWorkerTests(unittest.TestCase):
    def test_stateless_calls_do_not_share_namespace(self) -> None:
        first = _run_worker("x = 21", request_id="call-1")
        self.assertEqual(first["status"], "ok")
        self.assertIsNone(first["result"])

        second = _run_worker("x * 2", request_id="call-2")
        self.assertEqual(second["status"], "error")
        self.assertIsNotNone(second["error"])
        assert second["error"] is not None
        self.assertEqual(second["error"]["code"], "PYTHON_EXCEPTION")
        self.assertIn("NameError", second["error"]["message"])

    def test_import_and_final_expression(self) -> None:
        response = _run_worker("import math; math.sqrt(81)", request_id="call-3")
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["result"], 9.0)

    def test_python_exception(self) -> None:
        response = _run_worker('raise ValueError("boom")', request_id="call-4")
        self.assertEqual(response["status"], "error")
        assert response["error"] is not None
        self.assertEqual(response["error"]["code"], "PYTHON_EXCEPTION")
        self.assertIn("boom", response["error"]["message"])
        self.assertIn("ValueError", response["error"]["traceback"] or "")

    def test_stdout_and_json_result(self) -> None:
        response = _run_worker('print("ok"); {"answer": 42}', request_id="call-5")
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["stdout"], "ok\n")
        self.assertEqual(response["result"], {"answer": 42})

    def test_multiline_function_definition(self) -> None:
        code = (
            "def add(a, b):\n"
            "    return a + b\n"
            "add(2, 3)"
        )
        response = _run_worker(code)
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["result"], 5)

    def test_unicode_in_code_stdout_and_result(self) -> None:
        response = _run_worker('print("你好"); "🎉"')
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["stdout"], "你好\n")
        self.assertEqual(response["result"], "🎉")

    def test_captures_stderr(self) -> None:
        response = _run_worker("import sys; sys.stderr.write('err'); 1")
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["stderr"], "err")
        self.assertEqual(response["result"], 1)

    def test_no_final_expression_returns_null_result(self) -> None:
        response = _run_worker("x = 1\nprint(x)")
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["stdout"], "1\n")
        self.assertIsNone(response["result"])

    def test_inputs_are_injected(self) -> None:
        response = _run_worker(
            "inputs['x'] + inputs['y']",
            inputs={"x": 10, "y": 32},
        )
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["result"], 42)

    def test_deep_nested_json_value(self) -> None:
        response = _run_worker("{'a': {'b': {'c': {'d': 1}}}}")
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["result"], {"a": {"b": {"c": {"d": 1}}}})

    def test_circular_reference_returns_bounded_repr(self) -> None:
        response = _run_worker("a = []\na.append(a)\na")
        self.assertEqual(response["status"], "ok")
        self.assertIsInstance(response["result"], str)
        assert isinstance(response["result"], str)
        self.assertIn("[...]", response["result"])
        self.assertLessEqual(
            len(json.dumps(response["result"], ensure_ascii=False).encode("utf-8")),
            MAX_RESULT_BYTES,
        )

    def test_truncates_infinite_stdout(self) -> None:
        response = _run_worker("print('x' * 200_000)")
        self.assertEqual(response["status"], "ok")
        self.assertTrue(response["truncated"]["stdout"])
        self.assertLessEqual(len(response["stdout"].encode("utf-8")), MAX_STDOUT_BYTES)

    def test_truncates_large_stderr(self) -> None:
        response = _run_worker("import sys; sys.stderr.write('e' * 200_000); 0")
        self.assertEqual(response["status"], "ok")
        self.assertTrue(response["truncated"]["stderr"])
        self.assertLessEqual(len(response["stderr"].encode("utf-8")), MAX_STDERR_BYTES)

    def test_truncates_large_traceback(self) -> None:
        depth = 750
        lines = [f"def f{i}():\n    return f{i + 1}()" for i in range(depth)]
        lines.append(f"def f{depth}():\n    raise RuntimeError('x' * 5000)")
        lines.append("f0()")
        response = _run_worker("\n".join(lines))
        self.assertEqual(response["status"], "error")
        assert response["error"] is not None
        traceback = response["error"]["traceback"] or ""
        self.assertTrue(response["truncated"]["traceback"])
        self.assertLessEqual(len(traceback.encode("utf-8")), MAX_TRACEBACK_BYTES)

    def test_invalid_request_rejects_oversized_code(self) -> None:
        request = {
            "request_id": "bad-1",
            "code": "x" * (64 * 1024 + 1),
            "inputs": {},
        }
        completed = subprocess.run(
            [sys.executable, "-m", "deep_agent.sandbox.worker"],
            input=encode_frame(request),
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)
        response = read_frame(io.BytesIO(completed.stdout))
        self.assertEqual(response["status"], "error")
        assert response["error"] is not None
        self.assertEqual(response["error"]["code"], "INVALID_REQUEST")

    def test_response_echoes_request_id(self) -> None:
        response = _run_worker("1 + 1", request_id="req-abc")
        self.assertEqual(response["request_id"], "req-abc")

    @unittest.skipUnless(_PANDAS_AVAILABLE, "pandas not installed")
    def test_dataframe_preview(self) -> None:
        code = (
            "import pandas as pd\n"
            "pd.DataFrame({'a': range(20), 'b': list('abcdefghijklmnopqrst')})"
        )
        response = _run_worker(code)
        self.assertEqual(response["status"], "ok")
        result = response["result"]
        self.assertIsInstance(result, dict)
        assert isinstance(result, dict)
        self.assertEqual(result["kind"], "dataframe")
        self.assertEqual(result["shape"], [20, 2])
        self.assertEqual(result["columns"], ["a", "b"])
        self.assertIn("dtypes", result)
        self.assertLessEqual(len(result["records"]), 10)
        self.assertEqual(len(result["records"]), 10)

    @unittest.skipUnless(_PANDAS_AVAILABLE, "pandas not installed")
    def test_series_preview(self) -> None:
        code = "import pandas as pd\npd.Series(range(25), name='values')"
        response = _run_worker(code)
        self.assertEqual(response["status"], "ok")
        result = response["result"]
        self.assertIsInstance(result, dict)
        assert isinstance(result, dict)
        self.assertEqual(result["kind"], "series")
        self.assertEqual(result["name"], "values")
        self.assertEqual(result["length"], 25)
        self.assertIn("dtype", result)
        self.assertLessEqual(len(result["records"]), 10)
        self.assertEqual(len(result["records"]), 10)


if __name__ == "__main__":
    unittest.main()
