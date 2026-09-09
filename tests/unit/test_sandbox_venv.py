from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from deep_agent.options import OPTION
from deep_agent.sandbox import venv as sandbox_venv

_GUNICORN_CONF_PATH = Path(__file__).resolve().parents[2] / "deploy" / "gunicorn.conf.py"


def _load_gunicorn_conf():
    spec = importlib.util.spec_from_file_location(
        "deep_agent_gunicorn_conf",
        _GUNICORN_CONF_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SandboxVenvEnsureTests(unittest.TestCase):
    def test_startup_ensure_skipped_when_flag_disabled(self) -> None:
        previous = OPTION.agent.sandbox_ensure_venv
        OPTION.agent.sandbox_ensure_venv = False
        try:
            self.assertIsNone(sandbox_venv.ensure_sandbox_interpreter_for_startup())
        finally:
            OPTION.agent.sandbox_ensure_venv = previous

    def test_startup_ensure_skipped_when_sandbox_disabled(self) -> None:
        previous_enabled = OPTION.agent.sandbox_enabled
        previous_ensure = OPTION.agent.sandbox_ensure_venv
        OPTION.agent.sandbox_enabled = False
        OPTION.agent.sandbox_ensure_venv = True
        try:
            self.assertIsNone(sandbox_venv.ensure_sandbox_interpreter_for_startup())
        finally:
            OPTION.agent.sandbox_enabled = previous_enabled
            OPTION.agent.sandbox_ensure_venv = previous_ensure

    def test_startup_ensure_delegates_when_enabled(self) -> None:
        previous_ensure = OPTION.agent.sandbox_ensure_venv
        OPTION.agent.sandbox_ensure_venv = True
        try:
            with patch.object(
                sandbox_venv,
                "ensure_sandbox_venv",
                return_value=Path("/tmp/fake-sandbox-python"),
            ) as ensure_mock:
                result = sandbox_venv.ensure_sandbox_interpreter_for_startup()
            ensure_mock.assert_called_once_with()
            self.assertEqual(result, Path("/tmp/fake-sandbox-python"))
        finally:
            OPTION.agent.sandbox_ensure_venv = previous_ensure

    def test_existing_interpreter_skips_setup(self) -> None:
        previous_python = OPTION.agent.sandbox_python
        OPTION.agent.sandbox_python = "/tmp/fake-sandbox-python"
        try:
            with patch.object(Path, "is_file", return_value=True):
                with patch("deep_agent.sandbox.venv.subprocess.run") as run_mock:
                    result = sandbox_venv.ensure_sandbox_venv()
            run_mock.assert_not_called()
            self.assertEqual(result, Path("/tmp/fake-sandbox-python"))
        finally:
            OPTION.agent.sandbox_python = previous_python

    def test_build_setup_env_uses_tsinghua_mirror_by_default(self) -> None:
        env = sandbox_venv.build_sandbox_setup_env(Path("/tmp/.venv-sandbox"))
        self.assertEqual(
            env["DEEP_AGENT_SANDBOX_PIP_INDEX_URL"],
            "https://pypi.tuna.tsinghua.edu.cn/simple",
        )
        self.assertEqual(
            env["DEEP_AGENT_SANDBOX_PIP_TRUSTED_HOST"],
            "pypi.tuna.tsinghua.edu.cn",
        )

    def test_build_setup_env_derives_trusted_host_when_cleared(self) -> None:
        previous_index = OPTION.agent.sandbox_pip_index_url
        previous_trusted = OPTION.agent.sandbox_pip_trusted_host
        OPTION.agent.sandbox_pip_index_url = "https://mirrors.aliyun.com/pypi/simple/"
        OPTION.agent.sandbox_pip_trusted_host = ""
        try:
            env = sandbox_venv.build_sandbox_setup_env(Path("/tmp/.venv-sandbox"))
            self.assertEqual(
                env["DEEP_AGENT_SANDBOX_PIP_INDEX_URL"],
                "https://mirrors.aliyun.com/pypi/simple/",
            )
            self.assertEqual(env["DEEP_AGENT_SANDBOX_PIP_TRUSTED_HOST"], "mirrors.aliyun.com")
        finally:
            OPTION.agent.sandbox_pip_index_url = previous_index
            OPTION.agent.sandbox_pip_trusted_host = previous_trusted

    def test_build_setup_env_omits_mirror_when_index_cleared(self) -> None:
        previous_index = OPTION.agent.sandbox_pip_index_url
        previous_trusted = OPTION.agent.sandbox_pip_trusted_host
        OPTION.agent.sandbox_pip_index_url = ""
        OPTION.agent.sandbox_pip_trusted_host = ""
        try:
            env = sandbox_venv.build_sandbox_setup_env(Path("/tmp/.venv-sandbox"))
            self.assertNotIn("DEEP_AGENT_SANDBOX_PIP_INDEX_URL", env)
            self.assertNotIn("DEEP_AGENT_SANDBOX_PIP_TRUSTED_HOST", env)
        finally:
            OPTION.agent.sandbox_pip_index_url = previous_index
            OPTION.agent.sandbox_pip_trusted_host = previous_trusted

    def test_missing_interpreter_exits_with_gunicorn_hint(self) -> None:
        previous_enabled = OPTION.agent.sandbox_enabled
        previous_backend = OPTION.agent.sandbox_backend
        previous_python = OPTION.agent.sandbox_python
        OPTION.agent.sandbox_enabled = True
        OPTION.agent.sandbox_backend = "subprocess-dev"
        OPTION.agent.sandbox_python = "/nonexistent/deep-agent-sandbox-python"
        try:
            with self.assertRaises(SystemExit) as ctx:
                sandbox_venv.preflight_sandbox_interpreter(ensure=False)
            message = str(ctx.exception)
            self.assertIn("scripts/sandbox_venv/setup.sh", message)
            self.assertIn("DEEP_AGENT_SANDBOX_ENSURE_VENV", message)
        finally:
            OPTION.agent.sandbox_enabled = previous_enabled
            OPTION.agent.sandbox_backend = previous_backend
            OPTION.agent.sandbox_python = previous_python


class GunicornSandboxStartupTests(unittest.TestCase):
    def test_deep_agent_uses_one_worker_for_process_local_run_cancellation(self) -> None:
        gunicorn_conf = _load_gunicorn_conf()

        self.assertEqual(gunicorn_conf.workers, 1)

    def test_on_starting_runs_sandbox_ensure_before_platform_hook(self) -> None:
        gunicorn_conf = _load_gunicorn_conf()
        calls: list[str] = []

        def fake_ensure() -> None:
            calls.append("ensure")

        original = gunicorn_conf._original_on_starting

        def fake_platform_on_starting(server) -> None:
            calls.append("platform")

        gunicorn_conf._original_on_starting = fake_platform_on_starting
        try:
            with patch.object(
                sandbox_venv,
                "ensure_sandbox_interpreter_for_startup",
                side_effect=fake_ensure,
            ):
                gunicorn_conf.on_starting(MagicMock())
        finally:
            gunicorn_conf._original_on_starting = original

        self.assertEqual(calls, ["ensure", "platform"])


if __name__ == "__main__":
    unittest.main()
