from __future__ import annotations

import os
import subprocess
from pathlib import Path

from deep_agent.core.paths import REPO_ROOT

__all__ = [
    "ensure_sandbox_interpreter_for_startup",
    "ensure_sandbox_venv",
    "preflight_sandbox_interpreter",
    "sandbox_setup_script",
]


def sandbox_setup_script() -> Path:
    return REPO_ROOT / "scripts" / "sandbox_venv" / "setup.sh"


def _subprocess_dev_enabled() -> bool:
    from deep_agent.options import OPTION

    if not OPTION.agent.sandbox_enabled:
        return False
    return str(OPTION.agent.sandbox_backend).strip() == "subprocess-dev"


def _sandbox_venv_dir() -> Path:
    from deep_agent.options import OPTION

    # bin/python or Scripts/python.exe -> venv root
    return Path(OPTION.agent.sandbox_python).parent.parent


def ensure_sandbox_venv(*, python: str | None = None) -> Path:
    """Create or refresh the sandbox venv via scripts/sandbox_venv/setup.sh."""
    from deep_agent.options import OPTION

    python_path = Path(OPTION.agent.sandbox_python)
    if python_path.is_file():
        return python_path

    script = sandbox_setup_script()
    if not script.is_file():
        raise FileNotFoundError(f"Sandbox venv setup script missing: {script}")

    venv_dir = _sandbox_venv_dir()
    lock_path = venv_dir / ".ensure.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    from filelock import FileLock

    with FileLock(str(lock_path), timeout=600, thread_local=False):
        if python_path.is_file():
            return python_path

        env = os.environ.copy()
        env["DEEP_AGENT_SANDBOX_VENV_DIR"] = str(venv_dir)
        cmd = ["bash", str(script)]
        if python:
            cmd.append(python)
        completed = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            env=env,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"sandbox venv setup failed with exit code {completed.returncode}; "
                f"see {script}"
            )
        if not python_path.is_file():
            raise FileNotFoundError(
                f"sandbox interpreter still missing after setup: {python_path}"
            )
        return python_path


def ensure_sandbox_interpreter_for_startup() -> Path | None:
    """Ensure sandbox venv when DEEP_AGENT_SANDBOX_ENSURE_VENV is enabled."""
    from deep_agent.options import OPTION

    if not _subprocess_dev_enabled():
        return None
    if not OPTION.agent.sandbox_ensure_venv:
        return None
    return ensure_sandbox_venv()


def preflight_sandbox_interpreter(*, ensure: bool = False) -> None:
    """Fail fast before uvicorn if subprocess-dev interpreter is missing."""
    from deep_agent.options import OPTION

    if not _subprocess_dev_enabled():
        return

    python_path = Path(OPTION.agent.sandbox_python)
    if python_path.is_file():
        return
    if ensure:
        ensure_sandbox_venv()
        return

    raise SystemExit(
        "Sandbox interpreter missing for subprocess-dev:\n"
        f"  {python_path}\n"
        "Create it first:\n"
        "  bash scripts/sandbox_venv/setup.sh\n"
        "Or re-run local_dev without --no-sandbox-env "
        "(auto-setup is on by default).\n"
        "For gunicorn deployments, set DEEP_AGENT_SANDBOX_ENSURE_VENV=true "
        "so the master process creates the venv before workers start.\n"
        "To skip sandbox entirely, set DEEP_AGENT_SANDBOX_ENABLED=false in the Profile."
    )
