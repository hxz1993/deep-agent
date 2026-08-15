from __future__ import annotations

from pathlib import Path

from deep_agent.options import DeepAgentOptions, OPTION
from deep_agent.sandbox.backend import PythonExecutionBackend
from deep_agent.sandbox.subprocess_dev.backend import SubprocessBackend

__all__ = ["build_sandbox_backend", "SandboxInterpreterError"]


class SandboxInterpreterError(RuntimeError):
    """Raised when the configured sandbox interpreter is missing or not a file."""


def build_sandbox_backend(
    options: DeepAgentOptions = OPTION,
) -> PythonExecutionBackend | None:
    agent = options.agent
    if not agent.sandbox_enabled:
        return None

    backend_name = str(agent.sandbox_backend).strip()
    if backend_name == "subprocess-dev":
        python_executable = str(agent.sandbox_python).strip()
        if not python_executable:
            raise SandboxInterpreterError(
                "DEEP_AGENT_SANDBOX_PYTHON is empty; create the sandbox venv "
                "(see scripts/sandbox_venv/README.md)"
            )
        python_path = Path(python_executable)
        if not python_path.is_file():
            raise SandboxInterpreterError(
                f"sandbox interpreter not found: {python_executable} "
                "(run scripts/sandbox_venv/setup.sh or set DEEP_AGENT_SANDBOX_PYTHON)"
            )
        return SubprocessBackend(
            python_executable=str(python_path),
            max_concurrency=agent.sandbox_max_concurrency,
            memory_limit_bytes=agent.sandbox_memory_mb * 1024 * 1024,
            cpu_limit_seconds=agent.sandbox_cpu_seconds,
            max_pids=agent.sandbox_max_pids or None,
        )

    # Stage C: go-judge backend will be registered here.
    raise ValueError(f"Unknown sandbox backend: {backend_name}")
