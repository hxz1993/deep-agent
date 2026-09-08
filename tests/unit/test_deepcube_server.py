from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

SERVER_FILE = Path(__file__).resolve().parents[2] / "mcp_servers" / "deepcube" / "server.py"


def _build_option_stub() -> SimpleNamespace:
    return SimpleNamespace(
        server=SimpleNamespace(base="https://test.deepfos.com/deepfos-server/"),
        srvdis=SimpleNamespace(
            enabled=True,
            implementation="eureka",
            cache_strategy="ranked",
            take_over=False,
        ),
        eureka=SimpleNamespace(server="http://eureka:8761/eureka"),
        nacos=SimpleNamespace(
            server="127.0.0.1:8848",
            cluster="DEFAULT",
            namespace="public",
            group="DEFAULT_GROUP",
            user="nacos",
            password="nacos-pass",
        ),
        k8s=SimpleNamespace(namespace="default"),
    )


def _load_server_module(option_stub: SimpleNamespace | None = None):
    fake_config = types.ModuleType("deepcube.mcp.config")
    fake_config.ENV_DISCOVERY = "DEEPCUBE_MCP_DISCOVERY"
    fake_config.ENV_ENV_URL = "DEEPCUBE_MCP_ENV_URL"

    fake_server = types.ModuleType("deepcube.mcp.server")
    fake_server.mcp = MagicMock()
    fake_server.finalize_mcp = MagicMock()

    fake_options_module = types.ModuleType("deepfos_platform.options")
    fake_options_module.OPTION = option_stub or _build_option_stub()

    spec = importlib.util.spec_from_file_location("deepcube_server", SERVER_FILE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "deepcube.mcp.config": fake_config,
            "deepcube.mcp.server": fake_server,
            "deepfos_platform.options": fake_options_module,
        },
    ):
        spec.loader.exec_module(module)
    return module


class DeepcubeServerLauncherTests(unittest.TestCase):
    def test_installed_package_exposes_unprefixed_raw_tool_names(self) -> None:
        script = """
import asyncio
import json
from deepcube.mcp.server import mcp

async def main():
    tools = await mcp.list_tools()
    print(json.dumps([tool.name for tool in tools]))

asyncio.run(main())
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )
        names = set(json.loads(result.stdout.strip().splitlines()[-1]))

        self.assertEqual(
            names,
            {
                "examples",
                "find_nd_member",
                "guide",
                "list_cube_dimensions",
                "list_cubes",
                "ping",
                "read",
                "resolve_members",
                "search",
                "search_dimension_members",
                "validate_expression",
            },
        )
        self.assertFalse(
            any(name.startswith("deepcube_") for name in names),
            "DeepAgent adds the server prefix; package tools must remain unprefixed",
        )

    def test_sync_env_builds_nacos_discovery_json(self) -> None:
        option_stub = _build_option_stub()
        option_stub.srvdis.implementation = "nacos"
        option_stub.srvdis.cache_strategy = "roundrobin"
        mod = _load_server_module(option_stub)
        with patch.dict(os.environ, {}, clear=True):
            mod.sync_env()
            discovery = json.loads(os.environ["DEEPCUBE_MCP_DISCOVERY"])
            self.assertTrue(discovery["enabled"])
            self.assertEqual(discovery["implementation"], "nacos")
            self.assertEqual(discovery["cache_strategy"], "roundrobin")
            self.assertEqual(discovery["nacos"]["server"], "127.0.0.1:8848")
            self.assertEqual(discovery["nacos"]["namespace"], "public")
            self.assertTrue(discovery["take_over"])

    def test_sync_env_sets_eureka_in_discovery_json(self) -> None:
        mod = _load_server_module()
        with patch.dict(os.environ, {}, clear=True):
            mod.sync_env()
            discovery = json.loads(os.environ["DEEPCUBE_MCP_DISCOVERY"])
            self.assertEqual(discovery["eureka"], "http://eureka:8761/eureka")

    def test_sync_env_skips_when_discovery_already_set(self) -> None:
        mod = _load_server_module()
        existing = '{"enabled":true,"implementation":"eureka"}'
        with patch.dict(
            os.environ,
            {
                "DEEPCUBE_MCP_DISCOVERY": existing,
            },
            clear=True,
        ):
            mod.sync_env()
            self.assertEqual(os.environ["DEEPCUBE_MCP_DISCOVERY"], existing)

    def test_sync_env_sets_env_url_for_local_discovery(self) -> None:
        option_stub = _build_option_stub()
        option_stub.srvdis.implementation = "local"
        mod = _load_server_module(option_stub)
        with patch.dict(os.environ, {}, clear=True):
            mod.sync_env()
            self.assertEqual(
                os.environ["DEEPCUBE_MCP_ENV_URL"],
                "https://test.deepfos.com/deepfos-server/",
            )
            discovery = json.loads(os.environ["DEEPCUBE_MCP_DISCOVERY"])
            self.assertFalse(discovery["enabled"])
            self.assertFalse(discovery["take_over"])
            self.assertEqual(discovery["implementation"], "local")

    def test_sync_env_local_from_export_env_skips_enabled_validation(self) -> None:
        mod = _load_server_module()
        with patch.dict(
            os.environ,
            {"EXPORT_SERVICE_DISCOVERY_IMPLEMENTATION": "local"},
            clear=True,
        ):
            mod.sync_env()
            discovery = json.loads(os.environ["DEEPCUBE_MCP_DISCOVERY"])
            self.assertFalse(discovery["enabled"])

    def test_sync_env_disabled_uses_gateway_without_takeover(self) -> None:
        option_stub = _build_option_stub()
        option_stub.srvdis.enabled = False
        mod = _load_server_module(option_stub)
        with patch.dict(os.environ, {}, clear=True):
            mod.sync_env()
            self.assertEqual(
                os.environ["DEEPCUBE_MCP_ENV_URL"],
                "https://test.deepfos.com/deepfos-server/",
            )
            discovery = json.loads(os.environ["DEEPCUBE_MCP_DISCOVERY"])
            self.assertFalse(discovery["enabled"])
            self.assertFalse(discovery["take_over"])
            self.assertEqual(discovery["implementation"], "eureka")


if __name__ == "__main__":
    unittest.main()
