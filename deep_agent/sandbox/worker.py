from __future__ import annotations

import ast
import contextlib
import io
import sys
import traceback
from typing import Any, Iterator

from deep_agent.sandbox.framing import FramingError, read_frame, write_frame
from deep_agent.sandbox.serialization import serialize_result, truncate_utf8
from deep_agent.sandbox.types import (
    ExecuteRequest,
    MAX_STDERR_BYTES,
    MAX_STDOUT_BYTES,
    MAX_TRACEBACK_BYTES,
    TruncationMetadata,
)

SandboxErrorPayload = dict[str, Any]


def split_final_expression(module: ast.Module) -> tuple[list[ast.stmt], ast.expr | None]:
    body = list(module.body)
    if not body:
        return [], None
    last = body[-1]
    if isinstance(last, ast.Expr):
        return body[:-1], last.value
    return body, None


@contextlib.contextmanager
def capture_stdio() -> Iterator[tuple[io.StringIO, io.StringIO]]:
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = stdout_capture
    sys.stderr = stderr_capture
    try:
        yield stdout_capture, stderr_capture
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def _exception_payload(exc: BaseException) -> SandboxErrorPayload:
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    tb, _ = truncate_utf8(tb, MAX_TRACEBACK_BYTES)
    return {
        "code": "PYTHON_EXCEPTION",
        "message": f"{type(exc).__name__}: {exc}",
        "traceback": tb,
    }


def execute_code(code: str, inputs: dict[str, Any]) -> tuple[str, str, Any | None, SandboxErrorPayload | None, TruncationMetadata]:
    truncated = TruncationMetadata()
    error: SandboxErrorPayload | None = None
    result: Any | None = None
    status = "ok"

    with capture_stdio() as (stdout_capture, stderr_capture):
        try:
            module = ast.parse(code, mode="exec")
            body, final_expr = split_final_expression(module)
            namespace: dict[str, Any] = {"inputs": inputs}
            if body:
                exec(
                    compile(ast.Module(body=body, type_ignores=[]), "<sandbox>", "exec"),
                    namespace,
                    namespace,
                )
            if final_expr is not None:
                value = eval(
                    compile(ast.Expression(body=final_expr), "<sandbox>", "eval"),
                    namespace,
                    namespace,
                )
                result, result_truncated = serialize_result(value)
                truncated = TruncationMetadata(
                    stdout=truncated.stdout,
                    stderr=truncated.stderr,
                    traceback=truncated.traceback,
                    result=result_truncated,
                )
        except SyntaxError as exc:
            status = "error"
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            tb, tb_truncated = truncate_utf8(tb, MAX_TRACEBACK_BYTES)
            truncated = TruncationMetadata(
                stdout=truncated.stdout,
                stderr=truncated.stderr,
                traceback=tb_truncated,
                result=truncated.result,
            )
            error = _exception_payload(exc)
        except Exception as exc:
            status = "error"
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            tb, tb_truncated = truncate_utf8(tb, MAX_TRACEBACK_BYTES)
            truncated = TruncationMetadata(
                stdout=truncated.stdout,
                stderr=truncated.stderr,
                traceback=tb_truncated,
                result=truncated.result,
            )
            error = _exception_payload(exc)

    stdout, stdout_truncated = truncate_utf8(stdout_capture.getvalue(), MAX_STDOUT_BYTES)
    stderr, stderr_truncated = truncate_utf8(stderr_capture.getvalue(), MAX_STDERR_BYTES)
    truncated = TruncationMetadata(
        stdout=truncated.stdout or stdout_truncated,
        stderr=truncated.stderr or stderr_truncated,
        traceback=truncated.traceback,
        result=truncated.result,
    )
    return stdout, stderr, result if status == "ok" else None, error, truncated


def _error_response(
    request_id: str,
    *,
    code: str,
    message: str,
    traceback_text: str | None = None,
) -> dict[str, Any]:
    tb, tb_truncated = (
        truncate_utf8(traceback_text, MAX_TRACEBACK_BYTES)
        if traceback_text is not None
        else ("", False)
    )
    return {
        "request_id": request_id,
        "status": "error",
        "stdout": "",
        "stderr": "",
        "result": None,
        "error": {
            "code": code,
            "message": message,
            "traceback": tb or None,
        },
        "truncated": TruncationMetadata(traceback=tb_truncated).model_dump(),
    }


def handle_request(frame: dict[str, Any]) -> dict[str, Any]:
    request_id = str(frame.get("request_id", ""))
    try:
        request = ExecuteRequest.model_validate(frame)
    except Exception as exc:
        return _error_response(
            request_id,
            code="INVALID_REQUEST",
            message=str(exc),
        )

    stdout, stderr, result, error, truncated = execute_code(
        request.code,
        dict(request.inputs),
    )
    if error is not None:
        return {
            "request_id": request.request_id,
            "status": "error",
            "stdout": stdout,
            "stderr": stderr,
            "result": None,
            "error": error,
            "truncated": truncated.model_dump(),
        }
    return {
        "request_id": request.request_id,
        "status": "ok",
        "stdout": stdout,
        "stderr": stderr,
        "result": result,
        "error": None,
        "truncated": truncated.model_dump(),
    }


def main() -> None:
    try:
        request_frame = read_frame(sys.stdin.buffer)
    except FramingError as exc:
        response = _error_response(
            "",
            code="INVALID_REQUEST",
            message=str(exc),
        )
        write_frame(sys.stdout.buffer, response)
        return

    response = handle_request(request_frame)
    write_frame(sys.stdout.buffer, response)


if __name__ == "__main__":
    main()
