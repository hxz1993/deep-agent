from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from deep_agent.local_dev import preflight_sandbox_interpreter
from deep_agent.options import OPTION


class LocalDevSandboxPreflightTests(unittest.TestCase):
    def test_disabled_sandbox_skips_preflight(self) -> None:
        previous = OPTION.agent.sandbox_enabled
        OPTION.agent.sandbox_enabled = False
        try:
            preflight_sandbox_interpreter(ensure=False)
        finally:
            OPTION.agent.sandbox_enabled = previous

    def test_missing_interpreter_exits_with_setup_hint(self) -> None:
        previous_enabled = OPTION.agent.sandbox_enabled
        previous_backend = OPTION.agent.sandbox_backend
        previous_python = OPTION.agent.sandbox_python
        OPTION.agent.sandbox_enabled = True
        OPTION.agent.sandbox_backend = "subprocess-dev"
        OPTION.agent.sandbox_python = "/nonexistent/deep-agent-sandbox-python"
        try:
            with self.assertRaises(SystemExit) as ctx:
                preflight_sandbox_interpreter(ensure=False)
            message = str(ctx.exception)
            self.assertIn("scripts/sandbox_venv/setup.sh", message)
            self.assertIn("--no-sandbox-env", message)
        finally:
            OPTION.agent.sandbox_enabled = previous_enabled
            OPTION.agent.sandbox_backend = previous_backend
            OPTION.agent.sandbox_python = previous_python

    def test_existing_interpreter_passes(self) -> None:
        previous_enabled = OPTION.agent.sandbox_enabled
        previous_backend = OPTION.agent.sandbox_backend
        previous_python = OPTION.agent.sandbox_python
        OPTION.agent.sandbox_enabled = True
        OPTION.agent.sandbox_backend = "subprocess-dev"
        OPTION.agent.sandbox_python = "/tmp/fake-sandbox-python"
        try:
            with patch.object(Path, "is_file", return_value=True):
                preflight_sandbox_interpreter(ensure=False)
        finally:
            OPTION.agent.sandbox_enabled = previous_enabled
            OPTION.agent.sandbox_backend = previous_backend
            OPTION.agent.sandbox_python = previous_python


if __name__ == "__main__":
    unittest.main()
