from __future__ import annotations

import os
from pathlib import Path

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
    return Path(__file__).resolve().parents[3]


def build_worker_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key in _WHITELISTED_ENV_KEYS:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    env["PYTHONNOUSERSITE"] = "1"
    # Do not inherit macOS system/desktop proxy routes (often a loopback Clash
    # listener). Public traffic connects directly and is checked by the worker
    # socket guard on every connection.
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    # Backend-controlled import path for the worker module; never inherit host PYTHONPATH.
    env["PYTHONPATH"] = str(repo_root())
    env.pop("PYTHONHOME", None)
    return env
