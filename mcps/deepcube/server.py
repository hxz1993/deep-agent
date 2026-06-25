"""Builtin stdio launcher for the installed ``deepcube-mcp`` package.

Delegates to ``deepcube.mcp.server.main``. Runtime options are owned by
``deepcube-mcp`` and read from ``DEEPCUBE_MCP_*`` env vars (see
``deepcube.mcp.config``). This launcher only:

1) applies stdio-friendly defaults for builtin usage;
2) bridges discovery settings from ``deepfos_platform.options.OPTION`` when
   ``DEEPCUBE_MCP_DISCOVERY`` is not explicitly configured.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from deepcube.mcp.config import (
    ENV_DISCOVERY,
    ENV_DISCOVERY_ENABLED,
    ENV_ENV_URL,
    ENV_EUREKA,
    ENV_TRANSPORT,
)
from deepfos_platform.options import OPTION
from deep_agent.lib.const import REQUEST_HEADERS_ENV


def _optional_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def bridge_platform_discovery_env() -> None:
    """Map ``OPTION`` discovery settings into ``DEEPCUBE_MCP_*`` when unset."""
    if _optional_value(os.getenv(ENV_DISCOVERY)):
        return

    discovery: dict = {}

    if ENV_DISCOVERY_ENABLED not in os.environ:
        os.environ[ENV_DISCOVERY_ENABLED] = str(bool(OPTION.srvdis.enabled)).lower()
    discovery["enabled"] = bool(OPTION.srvdis.enabled)

    impl = _optional_value(OPTION.srvdis.implementation)
    if impl is not None:
        discovery["implementation"] = impl
        if impl == 'local':
            os.environ.setdefault(ENV_ENV_URL, OPTION.server.base)
        elif impl == 'eureka':
            eureka = _optional_value(OPTION.eureka.server)
            if eureka:
                if ENV_EUREKA not in os.environ:
                    os.environ[ENV_EUREKA] = eureka
                discovery["eureka"] = eureka
        elif impl == 'nacos':
            discovery["nacos"] = nacos = {}
            for value, key in (
                (OPTION.nacos.server, "server"),
                (OPTION.nacos.cluster, "cluster"),
                (OPTION.nacos.namespace, "namespace"),
                (OPTION.nacos.group, "group"),
                (OPTION.nacos.user, "user"),
                (OPTION.nacos.password, "password"),
            ):
                val = _optional_value(value)
                if val is not None:
                    nacos[key] = val
        elif impl == 'k8s':
            k8s_ns = _optional_value(OPTION.k8s.namespace)
            if k8s_ns:
                discovery["k8s"] = {"namespace": k8s_ns}

    cache = _optional_value(OPTION.srvdis.cache_strategy)
    if cache is not None:
        discovery["cache_strategy"] = cache

    # deepcube-mcp currently reads take_over only from DEEPCUBE_MCP_DISCOVERY JSON.
    discovery["take_over"] = True

    if discovery and ENV_DISCOVERY not in os.environ:
        os.environ[ENV_DISCOVERY] = json.dumps(discovery, ensure_ascii=False)


def _maybe_extract_json_object(value: str) -> dict | None:
    if value is None:
        return
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def bridge_agent_request_headers_env() -> None:
    """Map deep-agent startup headers into ``DEEPCUBE_MCP_HEADERS`` when unset."""
    from deepcube.mcp.config import ENV_HEADERS, extract_deepfos_headers

    payload = _maybe_extract_json_object(ENV_HEADERS) or {}
    request_payload = _maybe_extract_json_object(os.getenv(REQUEST_HEADERS_ENV)) or {}
    payload.update(request_payload)

    mapped = extract_deepfos_headers({str(k): str(v) for k, v in payload.items()})
    if mapped:
        os.environ[ENV_HEADERS] = json.dumps(mapped, ensure_ascii=False)


def ensure_stdio_defaults() -> None:
    """Builtin launcher defaults; deepcube-mcp itself defaults transport to http."""
    os.environ.setdefault(ENV_TRANSPORT, "stdio")


def main_entry() -> None:
    ensure_stdio_defaults()
    transport = os.environ.get(ENV_TRANSPORT, "stdio").lower()
    if transport in ("stdio", ""):
        bridge_agent_request_headers_env()
    bridge_platform_discovery_env()

    if transport == "http":
        _root = Path(__file__).resolve().parents[2]
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        from mcps._run import start_registry_ttl_watcher

        start_registry_ttl_watcher()

    from deepcube.mcp.server import main as deepcube_main

    try:
        deepcube_main(sys.argv[1:])
    except KeyboardInterrupt:
        print("[mcp] exited deepcube", flush=True)


if __name__ == "__main__":
    main_entry()
