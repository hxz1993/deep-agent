from __future__ import annotations

from pathlib import Path

from deep_agent.core.paths import REPO_ROOT

_SANDBOX_PYTHON_CANDIDATES = (
    REPO_ROOT / ".venv-sandbox" / "bin" / "python",
    REPO_ROOT / ".venv-sandbox" / "Scripts" / "python.exe",
)


def sandbox_test_python() -> str:
    """Return the independent sandbox interpreter used by live sandbox tests."""
    for candidate in _SANDBOX_PYTHON_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError(
        "sandbox test interpreter missing; run: bash scripts/sandbox_venv/setup.sh"
    )


FAKE_SANDBOX_PYTHON = "/opt/deep-agent-sandbox/bin/python"
