"""Builtin stdio launcher for the installed ``deepcube-mcp`` package.

Delegates to ``deepcube.mcp.server:main`` with stdio transport, a fixed
DeepFOS env URL, and optional static request headers for live metadata tools.

Static headers are required because ``deepcube-mcp`` stdio mode does not yet
read per-request ``_meta.headers`` from deep-agent. Configure them via the
``DEEPCUBE_MCP_HEADERS`` environment variable (JSON object), e.g. in the
``mcp_servers.config.env`` field::

    {"app": "demo", "space": "demo", "token": "..."}

Run directly::

    set DEEPCUBE_MCP_HEADERS={"app":"demo","space":"demo","token":"..."}
    python mcps/deepcube/server.py
"""

from __future__ import annotations

import json
import os
import sys

DEFAULT_ENV_URL = "https://test.deepfos.com/deepfos-server/"
ENV_TRANSPORT = "DEEPCUBE_MCP_TRANSPORT"
ENV_ENV_URL = "DEEPCUBE_MCP_ENV_URL"
ENV_HEADERS = "DEEPCUBE_MCP_HEADERS"


def build_argv(extra: list[str] | None = None) -> list[str]:
    """Build CLI args for ``deepcube.mcp.server.main`` (without program name)."""
    if "--transport" in (extra or []):
        argv: list[str] = list(extra or [])
    else:
        argv = ["--transport", "stdio", *(extra or [])]

    if "--env-url" not in argv:
        env_url = os.getenv(ENV_ENV_URL, DEFAULT_ENV_URL).strip()
        if env_url:
            argv[0:0] = ["--env-url", env_url]

    if "--headers" not in argv:
        headers_json = os.getenv(ENV_HEADERS, "").strip()
        if headers_json:
            _validate_headers_json(headers_json)
            argv.extend(["--headers", headers_json])

    return argv


def _validate_headers_json(raw: str) -> None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {ENV_HEADERS}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"{ENV_HEADERS} must be a JSON object")


def main_entry() -> None:
    os.environ.setdefault(ENV_TRANSPORT, "stdio")
    os.environ.setdefault(ENV_ENV_URL, DEFAULT_ENV_URL)

    from deepcube.mcp.server import main as deepcube_main

    deepcube_main(build_argv(sys.argv[1:]))


if __name__ == "__main__":
    main_entry()
