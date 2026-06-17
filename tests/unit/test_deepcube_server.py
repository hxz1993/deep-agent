from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

SERVER_FILE = Path(__file__).resolve().parents[2] / "mcps" / "deepcube" / "server.py"


def _build_option_stub() -> SimpleNamespace:
    return SimpleNamespace(
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
    fake_config.ENV_TRANSPORT = "DEEPCUBE_MCP_TRANSPORT"
    fake_config.ENV_ENV_URL = "DEEPCUBE_MCP_ENV_URL"
    fake_config.ENV_DISCOVERY = "DEEPCUBE_MCP_DISCOVERY"
    fake_config.ENV_DISCOVERY_ENABLED = "DEEPCUBE_MCP_DISCOVERY_ENABLED"
    fake_config.ENV_EUREKA = "DEEPCUBE_MCP_EUREKA"

    fake_options_module = types.ModuleType("deepfos_platform.options")
    fake_options_module.OPTION = option_stub or _build_option_stub()

    spec = importlib.util.spec_from_file_location("deepcube_server", SERVER_FILE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "deepcube.mcp.config": fake_config,
            "deepfos_platform.options": fake_options_module,
        },
    ):
        spec.loader.exec_module(module)
    return module


class DeepcubeServerLauncherTests(unittest.TestCase):
    def test_ensure_stdio_defaults(self) -> None:
        mod = _load_server_module()
        with patch.dict(os.environ, {}, clear=True):
            mod.ensure_stdio_defaults()
            self.assertEqual(os.environ["DEEPCUBE_MCP_TRANSPORT"], "stdio")

    def test_ensure_stdio_defaults_do_not_override(self) -> None:
        mod = _load_server_module()
        with patch.dict(
            os.environ,
            {
                "DEEPCUBE_MCP_TRANSPORT": "http",
                "DEEPCUBE_MCP_ENV_URL": "https://custom.example/",
            },
            clear=True,
        ):
            mod.ensure_stdio_defaults()
            self.assertEqual(os.environ["DEEPCUBE_MCP_TRANSPORT"], "http")
            self.assertEqual(os.environ["DEEPCUBE_MCP_ENV_URL"], "https://custom.example/")

    def test_bridge_platform_discovery_builds_json(self) -> None:
        option_stub = _build_option_stub()
        option_stub.srvdis.implementation = "nacos"
        option_stub.srvdis.cache_strategy = "roundrobin"
        mod = _load_server_module(option_stub)
        with patch.dict(os.environ, {}, clear=True):
            mod.bridge_platform_discovery_env()
            discovery = json.loads(os.environ["DEEPCUBE_MCP_DISCOVERY"])
            self.assertTrue(discovery["enabled"])
            self.assertEqual(discovery["implementation"], "nacos")
            self.assertEqual(discovery["cache_strategy"], "roundrobin")
            self.assertEqual(discovery["nacos"]["server"], "127.0.0.1:8848")
            self.assertEqual(discovery["nacos"]["namespace"], "public")
            self.assertEqual(os.environ["DEEPCUBE_MCP_DISCOVERY_ENABLED"], "true")

    def test_bridge_platform_eureka(self) -> None:
        mod = _load_server_module()
        with patch.dict(os.environ, {}, clear=True):
            mod.bridge_platform_discovery_env()
            self.assertEqual(os.environ["DEEPCUBE_MCP_EUREKA"], "http://eureka:8761/eureka")
            discovery = json.loads(os.environ["DEEPCUBE_MCP_DISCOVERY"])
            self.assertEqual(discovery["eureka"], "http://eureka:8761/eureka")

    def test_bridge_skips_when_discovery_already_set(self) -> None:
        mod = _load_server_module()
        existing = '{"enabled":true,"implementation":"eureka"}'
        with patch.dict(
            os.environ,
            {
                "DEEPCUBE_MCP_DISCOVERY": existing,
            },
            clear=True,
        ):
            mod.bridge_platform_discovery_env()
            self.assertEqual(os.environ["DEEPCUBE_MCP_DISCOVERY"], existing)

    def test_main_entry_delegates_argv(self) -> None:
        mod = _load_server_module()
        mock_main = MagicMock()
        fake_server_module = MagicMock()
        fake_server_module.main = mock_main

        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(mod, "ensure_stdio_defaults"),
            patch.object(mod, "bridge_agent_request_headers_env"),
            patch.object(mod, "bridge_platform_discovery_env"),
            patch.dict(sys.modules, {"deepcube.mcp.server": fake_server_module}),
            patch.object(sys, "argv", ["server.py", "--transport", "stdio"]),
        ):
            mod.main_entry()

        mock_main.assert_called_once_with(["--transport", "stdio"])


if __name__ == "__main__":
    unittest.main()
