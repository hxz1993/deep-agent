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

from deepcube.mcp.config import (
    ENV_DISCOVERY,
    ENV_ENV_URL,
)
from deepcube.mcp.server import mcp, finalize_mcp
from deepfos_platform.options import OPTION


def _optional_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def sync_env() -> None:
    """Map ``OPTION`` discovery settings into ``DEEPCUBE_MCP_*`` when unset."""
    if ENV_DISCOVERY in os.environ:
        return

    impl = _optional_value(os.environ.get("EXPORT_SERVICE_DISCOVERY_IMPLEMENTATION"))
    if impl is None:
        impl = _optional_value(OPTION.srvdis.implementation)

    # ``local`` bypasses Eureka/Nacos/K8s; reading ``OPTION.srvdis.enabled`` would
    # run deepfos_platform validation that does not accept ``local`` as a backend.
    if impl == "local":
        os.environ.setdefault(ENV_ENV_URL, OPTION.server.base)
        os.environ[ENV_DISCOVERY] = json.dumps(
            {"enabled": False, "implementation": "local", "take_over": False},
            ensure_ascii=False,
        )
        return

    discovery_enabled = bool(OPTION.srvdis.enabled)
    if not discovery_enabled:
        os.environ.setdefault(ENV_ENV_URL, OPTION.server.base)
        discovery = {"enabled": False, "take_over": False}
        if impl is not None:
            discovery["implementation"] = impl
        cache = _optional_value(OPTION.srvdis.cache_strategy)
        if cache is not None:
            discovery["cache_strategy"] = cache
        os.environ[ENV_DISCOVERY] = json.dumps(discovery, ensure_ascii=False)
        return

    discovery: dict = {}

    discovery["enabled"] = discovery_enabled
    if impl is not None:
        discovery["implementation"] = impl
        if impl == "eureka":
            eureka = _optional_value(OPTION.eureka.server)
            if eureka:
                discovery["eureka"] = eureka
        elif impl == "nacos":
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
        elif impl == "k8s":
            k8s_ns = _optional_value(OPTION.k8s.namespace)
            if k8s_ns:
                discovery["k8s"] = {"namespace": k8s_ns}

    cache = _optional_value(OPTION.srvdis.cache_strategy)
    if cache is not None:
        discovery["cache_strategy"] = cache

    # deepcube-mcp currently reads take_over only from DEEPCUBE_MCP_DISCOVERY JSON.
    discovery["take_over"] = True

    if discovery:
        os.environ[ENV_DISCOVERY] = json.dumps(discovery, ensure_ascii=False)



if __name__ == "__main__":
    from mcp_servers._run import run_fastmcp
    sync_env()
    finalize_mcp(["--transport", "http"])
    run_fastmcp(mcp, prog="deepcube")
