from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from deep_agent.capabilities.mcp.shared_registry import (
    McpSharedRegistry,
    _record_is_live,
    shared_mcp_identity,
)
from tests.helpers.builtin_mcp_fixture import use_test_builtin_mcp


class McpServerTtlShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_exits_after_registry_ttl_without_touch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = McpSharedRegistry(
                runtime_dir=Path(tmpdir),
                server_ttl=3.0,
                connect_timeout=20.0,
            )
            with use_test_builtin_mcp():
                await registry.ensure_server("echo_fixture", {})
            identity = shared_mcp_identity("echo_fixture")
            record = json.loads((Path(tmpdir) / f"{identity}.json").read_text(encoding="utf-8"))

            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                if not _record_is_live(record):
                    return
                await asyncio.sleep(0.5)

            self.fail("MCP server still listening after registry TTL")


if __name__ == "__main__":
    unittest.main()
