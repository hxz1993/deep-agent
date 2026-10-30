from __future__ import annotations

import ast
import contextlib
import io
import importlib
import importlib.machinery
import importlib.util
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Iterator

from deepagent_files import collect_published_files

from deep_agent.sandbox.framing import FramingError, read_frame, write_frame
from deep_agent.sandbox.plotting import install_plotting_defaults
from deep_agent.sandbox.serialization import serialize_result, truncate_utf8
from deep_agent.sandbox.types import (
    ExecuteRequest,
    MAX_RESULT_BYTES,
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


def execute_script(
    arguments: tuple[str, ...],
    script_entry: str,
) -> tuple[
    str,
    str,
    Any | None,
    SandboxErrorPayload | None,
    TruncationMetadata,
    int,
]:
    truncated = TruncationMetadata()
    error: SandboxErrorPayload | None = None
    result: Any | None = None
    exit_code = 0
    original_argv = list(sys.argv)
    original_path = list(sys.path)
    original_main = sys.modules.get("__main__")

    with capture_stdio() as (stdout_capture, stderr_capture):
        try:
            skill_root = Path("skill").resolve()
            entry = (skill_root / script_entry).resolve()
            if skill_root not in entry.parents or not entry.is_file():
                raise ValueError("Skill execution entry is missing or outside its directory")
            relative = entry.relative_to(skill_root)
            package_name = ".".join(relative.parent.parts)
            module_name = ".".join((*relative.parent.parts, entry.stem))
            sys.path[:0] = [str(entry.parent), str(skill_root)]
            package_entry = entry.stem == "__init__"
            # An initializer selected as the entry must not first run as a parent import.
            parent_package = package_name.rpartition(".")[0] if package_entry else package_name
            if parent_package:
                package = importlib.import_module(parent_package)
                package_paths = getattr(package, "__path__", ())
                expected_parent = entry.parent.parent if package_entry else entry.parent
                if str(expected_parent) not in {str(Path(path).resolve()) for path in package_paths}:
                    raise ValueError("Skill package resolution did not select the requested directory")
            # Load the exact indexed file, not a guessed import name: legal
            # entry names such as report.v2.py still retain their package.
            if package_entry and package_name:
                module_name = package_name
            spec = importlib.util.spec_from_file_location(
                module_name, entry,
                loader=importlib.machinery.SourceFileLoader(module_name, str(entry)),
                submodule_search_locations=[str(entry.parent)] if package_entry else None,
            )
            if spec is None or spec.loader is None:
                raise ValueError("Skill execution entry could not be loaded")
            module = importlib.util.module_from_spec(spec)
            module.__name__ = "__main__"
            module.__package__ = package_name
            sys.modules["__main__"] = module
            if package_entry and package_name:
                sys.modules[package_name] = module
            sys.argv = [str(entry), *arguments]
            try:
                code = spec.loader.get_code(spec.name)
                if code is None:
                    raise ValueError("Skill execution entry has no Python code")
                exec(code, module.__dict__)
            except SystemExit as exc:
                if exc.code not in (None, 0):
                    exit_code = int(exc.code) if isinstance(exc.code, int) else 1
                    error = _exception_payload(
                        RuntimeError(f"script exited with status {exit_code}")
                    )
        except Exception as exc:
            if exit_code == 0:
                exit_code = 1
            error = _exception_payload(exc)
        finally:
            sys.argv = original_argv
            sys.path[:] = original_path
            if original_main is not None:
                sys.modules["__main__"] = original_main
            else:
                sys.modules.pop("__main__", None)

        try:
            result_path = Path("outputs/result.json")
            if result_path.is_file():
                raw = result_path.read_bytes()
                if len(raw) > MAX_RESULT_BYTES:
                    raise ValueError(f"outputs/result.json exceeds {MAX_RESULT_BYTES} bytes")
                parsed = json.loads(raw.decode("utf-8"))
                result, result_truncated = serialize_result(parsed)
                truncated = TruncationMetadata(result=result_truncated)
        except Exception as exc:
            if exit_code == 0:
                exit_code = 1
            if error is None:
                error = _exception_payload(exc)

    stdout, stdout_truncated = truncate_utf8(stdout_capture.getvalue(), MAX_STDOUT_BYTES)
    stderr, stderr_truncated = truncate_utf8(stderr_capture.getvalue(), MAX_STDERR_BYTES)
    truncated = TruncationMetadata(
        stdout=stdout_truncated,
        stderr=stderr_truncated,
        traceback=truncated.traceback,
        result=truncated.result,
    )
    return stdout, stderr, result, error, truncated, exit_code


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

    if request.mode == "script":
        assert request.script_entry is not None
        stdout, stderr, result, error, truncated, exit_code = execute_script(
            request.arguments,
            request.script_entry,
        )
    else:
        stdout, stderr, result, error, truncated = execute_code(
            request.code,
            dict(request.inputs),
        )
        exit_code = 0
    published_files: list[dict[str, str | None]] = []
    if error is None:
        try:
            published_files = collect_published_files()
        except Exception as exc:
            error = _exception_payload(exc)
            exit_code = 1
    if error is not None:
        return {
            "request_id": request.request_id,
            "status": "error",
            "stdout": stdout,
            "stderr": stderr,
            "result": result,
            "error": error,
            "exit_code": exit_code,
            "truncated": truncated.model_dump(),
            "published_files": [],
        }
    return {
        "request_id": request.request_id,
        "status": "ok",
        "stdout": stdout,
        "stderr": stderr,
        "result": result,
        "error": None,
        "exit_code": exit_code,
        "truncated": truncated.model_dump(),
        "published_files": published_files,
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

    # Each invocation is a fresh process, so font registration is per worker.
    install_plotting_defaults()
    response = handle_request(request_frame)
    write_frame(sys.stdout.buffer, response)


if __name__ == "__main__":
    main()
