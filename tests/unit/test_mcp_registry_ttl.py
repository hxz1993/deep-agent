from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from mcp_servers._run import should_exit_on_registry_ttl


class RegistryTtlWatcherTests(unittest.TestCase):
    def test_should_exit_when_mtime_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            record = Path(tmpdir) / "builtin-echo_fixture.json"
            record.write_text("{}", encoding="utf-8")
            old = time.time() - 10_000
            os.utime(record, (old, old))
            with mock.patch.dict(
                os.environ,
                {
                    "DEEP_AGENT_MCP_REGISTRY_JSON": str(record),
                    "DEEP_AGENT_MCP_SERVER_TTL": "300",
                },
                clear=False,
            ):
                self.assertTrue(should_exit_on_registry_ttl())

    def test_should_not_exit_when_mtime_is_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            record = Path(tmpdir) / "builtin-echo_fixture.json"
            record.write_text("{}", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {
                    "DEEP_AGENT_MCP_REGISTRY_JSON": str(record),
                    "DEEP_AGENT_MCP_SERVER_TTL": "300",
                },
                clear=False,
            ):
                self.assertFalse(should_exit_on_registry_ttl())

    def test_should_not_exit_without_registry_env(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(should_exit_on_registry_ttl())


if __name__ == "__main__":
    unittest.main()
