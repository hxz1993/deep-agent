from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

SERVER_FILE = Path(__file__).resolve().parents[2] / "mcps" / "deepcube" / "server.py"


def _load_server_module():
    spec = importlib.util.spec_from_file_location("deepcube_server", SERVER_FILE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DeepcubeServerLauncherTests(unittest.TestCase):
    def test_build_argv_defaults(self) -> None:
        mod = _load_server_module()
        with patch.dict(os.environ, {}, clear=True):
            argv = mod.build_argv()
        self.assertEqual(
            argv,
            ["--env-url", mod.DEFAULT_ENV_URL, "--transport", "stdio"],
        )

    def test_build_argv_includes_headers_from_env(self) -> None:
        mod = _load_server_module()
        headers = '{"app":"demo","space":"demo","token":"secret"}'
        with patch.dict(os.environ, {"DEEPCUBE_MCP_HEADERS": headers}, clear=True):
            argv = mod.build_argv()
        self.assertIn("--headers", argv)
        self.assertEqual(argv[argv.index("--headers") + 1], headers)

    def test_build_argv_respects_explicit_flags(self) -> None:
        mod = _load_server_module()
        argv = mod.build_argv(["--transport", "stdio", "--env-url", "https://example.test/"])
        self.assertEqual(argv, ["--transport", "stdio", "--env-url", "https://example.test/"])


if __name__ == "__main__":
    unittest.main()
