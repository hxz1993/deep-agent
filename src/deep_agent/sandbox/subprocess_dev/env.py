from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from deep_agent.core.paths import SOURCE_ROOT
from deep_agent.sandbox.context_env import EXECUTION_CONTEXT_ENV

_WHITELISTED_ENV_KEYS = frozenset(
    {
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        "LC_NUMERIC",
        "LC_TIME",
        "TZ",
        "PYTHONUTF8",
        "PYTHONIOENCODING",
    }
)


def repo_root() -> Path:
    """Worker import root (legacy helper name, now the physical src directory)."""
    return SOURCE_ROOT


def build_worker_env(context: Mapping[str, str] | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in _WHITELISTED_ENV_KEYS:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    env["PYTHONNOUSERSITE"] = "1"
    # Do not inherit macOS system/desktop proxy routes (often a loopback Clash
    # listener). Network clients connect directly using the deployment's routes.
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    # Backend-controlled import path for the worker module; never inherit host PYTHONPATH.
    env["PYTHONPATH"] = str(repo_root())
    if context and EXECUTION_CONTEXT_ENV in context:
        env[EXECUTION_CONTEXT_ENV] = context[EXECUTION_CONTEXT_ENV]
    env.pop("PYTHONHOME", None)
    return env
