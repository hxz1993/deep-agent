from __future__ import annotations

import sys
from pathlib import Path

from deep_agent.core.paths import REPO_ROOT

_SANDBOX_PYTHON_CANDIDATES = (
    REPO_ROOT / ".venv-sandbox" / "bin" / "python",
    REPO_ROOT / ".venv-sandbox" / "Scripts" / "python.exe",
)

_cached_test_python: str | None = None


def repo_sandbox_python_path() -> Path:
    if sys.platform == "win32":
        return REPO_ROOT / ".venv-sandbox" / "Scripts" / "python.exe"
    return REPO_ROOT / ".venv-sandbox" / "bin" / "python"


def sandbox_test_python() -> str:
    """Return the independent sandbox interpreter used by live sandbox tests."""
    global _cached_test_python

    if _cached_test_python and Path(_cached_test_python).is_file():
        return _cached_test_python

    for candidate in _SANDBOX_PYTHON_CANDIDATES:
        if candidate.is_file():
            _cached_test_python = str(candidate)
            return _cached_test_python

    from deep_agent.options import OPTION
    from deep_agent.sandbox.venv import ensure_sandbox_venv

    target = str(repo_sandbox_python_path())
    previous_python = OPTION.agent.sandbox_python
    previous_ensure = OPTION.agent.sandbox_ensure_venv
    try:
        OPTION.agent.sandbox_python = target
        OPTION.agent.sandbox_ensure_venv = False
        _cached_test_python = str(ensure_sandbox_venv())
        return _cached_test_python
    finally:
        OPTION.agent.sandbox_python = previous_python
        OPTION.agent.sandbox_ensure_venv = previous_ensure


FAKE_SANDBOX_PYTHON = "/opt/deep-agent-sandbox/bin/python"
