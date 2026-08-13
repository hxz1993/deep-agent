from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from unittest.mock import patch

import psutil

from deep_agent.capabilities.mcp.builtin import load_builtin_mcp_tool_packages


TEST_BUILTIN_NAME = "echo_fixture"
TEST_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"
TEST_MCPS_DIR = TEST_FIXTURE_ROOT / "mcps"


@contextmanager
def use_test_builtin_mcp():
    """Redirect builtin discovery and spawn resolution to test-only fixtures."""
    load_builtin_mcp_tool_packages.cache_clear()
    try:
        with (
            patch(
                "deep_agent.capabilities.mcp.builtin_config.MCPS_DIR",
                TEST_MCPS_DIR,
            ),
            patch(
                "deep_agent.capabilities.mcp.resolver.DEEP_AGENT_ROOT",
                TEST_FIXTURE_ROOT,
            ),
        ):
            yield
    finally:
        load_builtin_mcp_tool_packages.cache_clear()


def stop_test_builtin_mcp(runtime_dir: Path) -> None:
    """Stop the test-only subprocess recorded in *runtime_dir*, if present."""
    record_path = runtime_dir / f"builtin-{TEST_BUILTIN_NAME}.json"
    if not record_path.is_file():
        return
    try:
        payload = json.loads(record_path.read_text(encoding="utf-8"))
        pid = int(payload.get("pid") or 0)
        process = psutil.Process(pid) if pid > 0 else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError, psutil.NoSuchProcess):
        return
    if process is None:
        return
    try:
        process.terminate()
        process.wait(timeout=5.0)
    except psutil.TimeoutExpired:
        process.kill()
        process.wait(timeout=5.0)
    except psutil.NoSuchProcess:
        pass
