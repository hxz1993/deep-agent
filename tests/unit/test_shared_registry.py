from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from deep_agent.capabilities.mcp.shared_registry import McpSharedRegistry, shared_mcp_identity
from deep_agent.core.paths import REPO_ROOT, SOURCE_ROOT
from deep_agent.lib.const import MCP_REGISTRY_JSON_ENV, MCP_SERVER_TTL_ENV


class SharedRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.registry = McpSharedRegistry(
            runtime_dir=Path(self._tmpdir.name),
            server_ttl=300.0,
            lock_timeout=5.0,
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_identity_paths(self) -> None:
        identity, json_path, lock_path = self.registry._paths("echo_fixture")
        self.assertEqual(identity, "builtin-echo_fixture")
        self.assertTrue(str(json_path).endswith("builtin-echo_fixture.json"))
        self.assertTrue(str(lock_path).endswith("builtin-echo_fixture.lock"))

    def test_drop_stale_record_removes_dead_server_entry(self) -> None:
        identity = shared_mcp_identity("echo_fixture")
        json_path = Path(self._tmpdir.name) / f"{identity}.json"
        json_path.write_text(
            json.dumps({"host": "127.0.0.1", "port": 19999, "pid": 1}),
            encoding="utf-8",
        )

        asyncio.run(self.registry._drop_stale_record(json_path, json_path.with_suffix(".lock")))
        self.assertFalse(json_path.is_file())

    def test_spawn_deepcube_uses_http_transport_and_ttl_env(self) -> None:
        captured: dict[str, object] = {}

        async def fake_create_subprocess_exec(command, *args, **kwargs):
            captured["command"] = command
            captured["args"] = args
            captured["env"] = kwargs.get("env")
            proc = MagicMock()
            proc.pid = 4242
            proc.returncode = None
            return proc

        registry = McpSharedRegistry(
            runtime_dir=Path(self._tmpdir.name),
            connect_timeout=1.0,
            server_ttl=120.0,
        )
        with patch(
            "deep_agent.capabilities.mcp.shared_registry.asyncio.create_subprocess_exec",
            side_effect=fake_create_subprocess_exec,
        ):
            with patch(
                "deep_agent.capabilities.mcp.shared_registry._wait_for_live_server",
                new=AsyncMock(),
            ):
                url = asyncio.run(registry.ensure_server("deepcube", {}))

        self.assertIn("/mcp", url)
        env = captured["env"]
        assert isinstance(env, dict)
        args = captured["args"]
        assert isinstance(args, tuple)
        self.assertIn("streamable-http", args)
        self.assertEqual(env.get(MCP_SERVER_TTL_ENV), "120.0")
        self.assertTrue(env.get(MCP_REGISTRY_JSON_ENV))
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep), [str(SOURCE_ROOT), str(REPO_ROOT)])


if __name__ == "__main__":
    unittest.main()
