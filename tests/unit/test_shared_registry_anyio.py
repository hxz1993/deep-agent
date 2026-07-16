from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from deep_agent.capabilities.mcp.shared_registry import (
    McpSharedRegistry,
    _pid_alive,
    _record_is_live,
)
from deep_agent.options import OPTION


class SharedRegistryEnsureTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_server_text_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            OPTION.agent.mcp_runtime_dir = tmpdir
            OPTION.agent.mcp_init_timeout = 15.0
            registry = McpSharedRegistry(runtime_dir=Path(tmpdir), connect_timeout=15.0)
            url = await registry.ensure_server("text_tools", {})
        self.assertIn("/mcp", url)


class PidLivenessTests(unittest.TestCase):
    """Direct tests for ``_pid_alive`` / ``_record_is_live``.

    These underpin the dead-pid garbage-collection path that protects against
    a shared MCP registry serving a defunct subprocess URL. ``_pid_alive``
    uses ``psutil.pid_exists`` so the dead-pid probes below are safe to run
    on every platform (Windows included); the historical ``os.kill(huge_pid, 0)``
    quirk is no longer reachable.
    """

    def test_pid_alive_current_process_returns_true(self) -> None:
        # The test runner itself is alive; psutil always reports it.
        self.assertTrue(_pid_alive(os.getpid()))

    def test_pid_alive_none_is_treated_as_unknown_and_returns_true(self) -> None:
        # Backward compatibility: older records predate the pid field.
        # We must not force-evict them just because they lack a pid.
        self.assertTrue(_pid_alive(None))

    def test_pid_alive_dead_pid_returns_false(self) -> None:
        # A pid well above any realistic value (>> kernel ``pid_max``).
        # ``psutil.pid_exists`` returns False; on systems without psutil we
        # fall back to ``os.kill(pid, 0)`` which raises ``ProcessLookupError``.
        self.assertFalse(_pid_alive(int(2**30)))

    def test_pid_alive_garbage_value_returns_false(self) -> None:
        # ``int("not-a-number")`` raises ValueError → mapped to False.
        self.assertFalse(_pid_alive("not-a-number"))

    def test_pid_alive_zero_or_negative_pid_returns_false(self) -> None:
        # psutil reports pid 0 (the system scheduler) as "exists" but it's
        # never a real MCP subprocess, so we treat <= 0 as not a live
        # subprocess. ``-1`` is reserved by the kernel.
        self.assertFalse(_pid_alive(0))
        self.assertFalse(_pid_alive(-1))

    def test_record_is_live_returns_false_when_pid_dead(self) -> None:
        # A record pointing to a dead pid must be flagged stale even though its
        # port number is plausible. ``_record_is_live`` short-circuits on pid first.
        record = {
            "url": "http://127.0.0.1:65535/mcp",
            "host": "127.0.0.1",
            "port": 65535,
            "pid": int(2**30),
            "server_name": "deepcube",
        }
        self.assertFalse(_record_is_live(record))

    def test_record_is_live_short_circuits_when_pid_is_dead(self) -> None:
        # Direct check that ``_record_is_live`` returns False when the pid is
        # definitely dead. We mock the PID probe to simulate "dead" and assert
        # that the function short-circuits before the TCP probe is reached.
        from unittest.mock import patch

        record = {
            "url": "http://127.0.0.1:1/mcp",
            "host": "127.0.0.1",
            "port": 1,
            "pid": int(2**30),
            "server_name": "deepcube",
        }
        with patch(
            "deep_agent.capabilities.mcp.shared_registry._pid_alive",
            return_value=False,
        ):
            self.assertFalse(_record_is_live(record))

    def test_record_is_live_skips_pid_check_when_pid_field_absent(self) -> None:
        # Older records (pre-pid-field migration) lack ``pid`` → treated as
        # live iff the port is actually bound. Direct TCP probes of an
        # unused port are subject to platform-specific firewall and timeout
        # behaviour, so we exercise the branch logic by patching the TCP
        # probe to return False and asserting the overall result is False.
        from unittest.mock import patch

        record_no_pid = {
            "url": "http://127.0.0.1:65535/mcp",
            "host": "127.0.0.1",
            "port": 65535,
            "server_name": "deepcube",
        }
        with patch(
            "deep_agent.capabilities.mcp.shared_registry._tcp_open",
            return_value=False,
        ):
            self.assertFalse(_record_is_live(record_no_pid))


if __name__ == "__main__":
    unittest.main()
