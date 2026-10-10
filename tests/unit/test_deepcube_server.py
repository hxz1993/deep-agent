from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
            access_key=None,
            secret_key=None,
            ram_role_name=None,
            auth_mode="password",
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
        option_stub.nacos.namespace = " public "
        option_stub.nacos.cluster = ""
        option_stub.nacos.group = "  "
        mod = _load_server_module(option_stub)
        with patch.dict(os.environ, {}, clear=True):
            mod.sync_env()
            discovery = json.loads(os.environ["DEEPCUBE_MCP_DISCOVERY"])
            self.assertTrue(discovery["enabled"])
            self.assertEqual(discovery["implementation"], "nacos")
            self.assertEqual(discovery["cache_strategy"], "roundrobin")
            self.assertEqual(
                discovery["nacos"],
                {
                    "server": "127.0.0.1:8848",
                    "namespace": "public",
                    "user": "nacos",
                    "password": "nacos-pass",
                    "auth_mode": "password",
                },
            )
            self.assertTrue(discovery["take_over"])

    def test_sync_env_sends_all_nacos_options_without_sdk_assignment(self) -> None:
        for mode in ("password", "aksk", "ram"):
            with self.subTest(mode=mode):
                option_stub = _build_option_stub()
                option_stub.srvdis.implementation = "nacos"
                option_stub.nacos.auth_mode = mode
                option_stub.nacos.access_key = " test-ak "
                option_stub.nacos.secret_key = " test-sk "
                option_stub.nacos.ram_role_name = "test-role"
                option_stub.nacos.password = " nacos-pass "
                mod = _load_server_module(option_stub)
                sdk_options = types.ModuleType("deepfos.options")
                sdk_options.set_option = MagicMock()
                with patch.dict(sys.modules, {"deepfos.options": sdk_options}), patch.dict(
                    os.environ, {}, clear=True
                ):
                    mod.sync_env()
                    discovery = json.loads(os.environ["DEEPCUBE_MCP_DISCOVERY"])
                    self.assertEqual(
                        discovery["nacos"],
                        {
                            "server": "127.0.0.1:8848",
                            "cluster": "DEFAULT",
                            "namespace": "public",
                            "group": "DEFAULT_GROUP",
                            "user": "nacos",
                            "password": " nacos-pass ",
                            "access_key": " test-ak ",
                            "secret_key": " test-sk ",
                            "ram_role_name": "test-role",
                            "auth_mode": mode,
                        },
                    )
                    self.assertTrue(discovery["take_over"])
                    sdk_options.set_option.assert_not_called()

    def test_sync_env_does_not_log_nacos_credentials(self) -> None:
        from loguru import logger

        option_stub = _build_option_stub()
        option_stub.srvdis.implementation = "nacos"
        option_stub.nacos.password = " test-password "
        option_stub.nacos.access_key = " test-ak "
        option_stub.nacos.secret_key = " test-sk "
        mod = _load_server_module(option_stub)
        output = io.StringIO()
        handler = logger.add(output, format="{message}")
        try:
            with patch.dict(os.environ, {}, clear=True), redirect_stdout(output), redirect_stderr(output):
                mod.sync_env()
            self.assertEqual(output.getvalue(), "")
        finally:
            logger.remove(handler)

    def test_sync_env_sends_dns_records_without_sdk_assignment(self) -> None:
        from deepfos_platform.discovery.dns import DNS_ENV_BY_SERVER

        option_stub = _build_option_stub()
        mod = _load_server_module(option_stub)
        environ = dict.fromkeys(DNS_ENV_BY_SERVER.values(), "https://service.example")
        environ.update({
            "EXPORT_SERVICE_DISCOVERY_IMPLEMENTATION": "dns",
            "EXPORT_DNS_APP": "https://app.example/api",
            "EXPORT_DNS_DIMENSION_1_2": "http://dimension.example:8080",
        })
        sdk_options = types.ModuleType("deepfos.options")
        sdk_options.set_option = MagicMock()
        with patch.dict(sys.modules, {"deepfos.options": sdk_options}), patch.dict(
            os.environ, environ, clear=True
        ):
            mod.sync_env()
            discovery = json.loads(os.environ["DEEPCUBE_MCP_DISCOVERY"])
            self.assertEqual(discovery["implementation"], "dns")
            self.assertTrue(discovery["enabled"])
            self.assertTrue(discovery["take_over"])
            self.assertEqual(discovery["cache_strategy"], "ranked")
            records = discovery["dns"]["records"]
            self.assertEqual(len(records), 50)
            self.assertEqual(records["APP-SERVER"], "https://app.example/api")
            self.assertEqual(records["DIMENSION-SERVER1-2"], "http://dimension.example:8080")
            self.assertEqual(records["WEB-GATEWAY-SERVER"], "https://service.example")
            self.assertEqual(os.environ["EXPORT_DNS_APP"], "https://app.example/api")
            self.assertNotIn("eureka", discovery)
            sdk_options.set_option.assert_not_called()

    def test_sync_env_sets_eureka_in_discovery_json(self) -> None:
        mod = _load_server_module()
        with patch.dict(os.environ, {}, clear=True):
            mod.sync_env()
            discovery = json.loads(os.environ["DEEPCUBE_MCP_DISCOVERY"])
            self.assertEqual(discovery["eureka"], "http://eureka:8761/eureka")

    def test_sync_env_skips_when_discovery_already_set(self) -> None:
        option_stub = _build_option_stub()
        option_stub.srvdis.implementation = "nacos"
        option_stub.nacos.auth_mode = "aksk"
        mod = _load_server_module(option_stub)
        sdk_options = types.ModuleType("deepfos.options")
        sdk_options.set_option = MagicMock()
        existing = '{"enabled":true,"implementation":"eureka"}'
        with patch.dict(sys.modules, {"deepfos.options": sdk_options}), patch.dict(
            os.environ,
            {
                "DEEPCUBE_MCP_DISCOVERY": existing,
            },
            clear=True,
        ):
            mod.sync_env()
            self.assertEqual(os.environ["DEEPCUBE_MCP_DISCOVERY"], existing)
            sdk_options.set_option.assert_not_called()

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
