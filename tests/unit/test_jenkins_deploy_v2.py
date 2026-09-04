from __future__ import annotations

import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from deep_agent import combined_runtime as runtime


ROOT = Path(__file__).resolve().parents[2]


class CombinedRuntimeTest(unittest.TestCase):
    def test_child_environment_connects_both_processes_with_one_token(self):
        with patch.object(runtime.secrets, "token_urlsafe", return_value="generated-token"):
            environ = runtime.build_child_env({
                "DEEP_AGENT_PYTHON_BIND": "0.0.0.0:31000",
                "DEEP_AGENT_CHANNEL_PORT": "8110",
            })
        self.assertEqual(environ[runtime.TOKEN_ENV], "generated-token")
        self.assertEqual(environ["DEEP_AGENT_CHANNEL_API_URL"], "http://127.0.0.1:31000")
        self.assertEqual(environ["DEEP_AGENT_CHANNEL_GATEWAY_URL"], "http://127.0.0.1:8110")
        self.assertEqual(environ["DEEP_AGENT_CHANNEL_HOST"], "127.0.0.1")

    def test_existing_internal_token_is_preserved(self):
        environ = runtime.build_child_env({runtime.TOKEN_ENV: "configured-token"})
        self.assertEqual(environ[runtime.TOKEN_ENV], "configured-token")

    def test_runtime_token_file_is_private_and_readable_by_check(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-combined-runtime-") as directory:
            token_file = Path(directory) / "runtime" / "token"
            environ = {
                runtime.TOKEN_ENV: "private-token",
                runtime.TOKEN_FILE_ENV: str(token_file),
            }
            runtime.write_runtime_token(environ)
            self.assertEqual(token_file.read_text(), "private-token")
            self.assertEqual(stat.S_IMODE(token_file.stat().st_mode), 0o600)
            self.assertEqual(
                runtime.read_runtime_token({runtime.TOKEN_FILE_ENV: str(token_file)}),
                "private-token",
            )

    def test_wait_for_health_fails_immediately_when_child_exits(self):
        process = Mock()
        process.poll.return_value = 7
        with self.assertRaisesRegex(RuntimeError, "exited during startup with code 7"):
            runtime.wait_for_health("API", "http://127.0.0.1:1/health", process, timeout=1)

    def test_check_requires_api_gateway_and_revision_002(self):
        environ = {
            runtime.TOKEN_ENV: "token",
            "DEEP_AGENT_PYTHON_PORT": "30098",
            "DEEP_AGENT_CHANNEL_PORT": "8010",
        }
        with patch.object(runtime, "fetch_health", side_effect=[
            {"status": "ok"},
            {"status": "ok", "database_revision": "002"},
        ]) as fetch:
            runtime.check_runtime(environ)
        self.assertEqual(fetch.call_args_list, [
            call("http://127.0.0.1:30098/health"),
            call("http://127.0.0.1:8010/health", token="token"),
        ])

    def test_check_command_waits_for_the_gateway_startup_race(self):
        with patch.object(runtime, "check_runtime", side_effect=[RuntimeError("starting"), None]) as check:
            with patch.object(runtime.time, "sleep"):
                runtime.wait_for_runtime_health(timeout=1)
        self.assertEqual(check.call_count, 2)

    def test_supervisor_starts_gateway_only_after_api_health(self):
        api = Mock(pid=101)
        gateway = Mock(pid=102)
        api.poll.return_value = None
        gateway.poll.return_value = 9
        child_env = runtime.build_child_env({runtime.TOKEN_ENV: "token"})
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-combined-runtime-") as directory:
            child_env[runtime.TOKEN_FILE_ENV] = str(Path(directory) / "token")
            with (
                patch.object(runtime, "build_child_env", return_value=child_env),
                patch.object(runtime.subprocess, "Popen", side_effect=[api, gateway]) as popen,
                patch.object(runtime, "wait_for_health", return_value={"status": "ok"}) as wait,
                patch.object(runtime, "stop_processes") as stop,
            ):
                self.assertEqual(runtime.run_combined(startup_timeout=1), 9)
        self.assertEqual(popen.call_args_list[0].args[0], runtime.API_COMMAND)
        self.assertEqual(popen.call_args_list[1].args[0], runtime.GATEWAY_COMMAND)
        self.assertIn("30098/health", wait.call_args_list[0].args[1])
        self.assertIn("8010/health", wait.call_args_list[1].args[1])
        stop.assert_called_once_with([api, gateway])


class JenkinsPipelineV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipeline = (ROOT / "docs/guides/jenkins/Jenkinsfile.v2").read_text()

    def test_embedded_shell_scripts_are_valid(self):
        scripts = re.findall(r"sh '''(.*?)'''", self.pipeline, flags=re.DOTALL)
        self.assertEqual(len(scripts), 4)
        for script in scripts:
            result = subprocess.run(
                ["/bin/sh", "-n"], input=script, text=True, capture_output=True, check=False
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_pipeline_keeps_v1_deployment_shape_without_node_python(self):
        self.assertNotIn("command -v python", self.pipeline)
        self.assertNotIn("deploy_v2.py", self.pipeline)
        self.assertNotIn("kubectl apply", self.pipeline)
        self.assertNotIn("kubectl patch", self.pipeline)
        self.assertIn('kubectl set image "deployment/$NAME"', self.pipeline)
        self.assertIn('kubectl rollout status "deployment/$NAME"', self.pipeline)
        self.assertIn("python -m deep_agent.combined_runtime --check", self.pipeline)

    def test_v2_image_uses_combined_runtime_but_v1_stays_api_only(self):
        dockerfile = re.search(
            r"writeFile file: 'Dockerfile', text: '''(.*?)'''", self.pipeline, flags=re.DOTALL
        )[1]
        self.assertIn('CMD ["python", "-m", "deep_agent.combined_runtime"]', dockerfile)
        v1 = (ROOT / "docs/guides/jenkins/Jenkinsfile.v1").read_text()
        self.assertIn('CMD ["gunicorn", "-c", "deep_agent/gunicorn.conf.py", "deep_agent.main:app"]', v1)
        self.assertNotIn("deep_agent.combined_runtime", v1)

    def test_only_three_existing_parameters_are_required(self):
        parameter_block = re.search(
            r"parameters \{(.*?)\n    \}\n    stages", self.pipeline, flags=re.DOTALL
        )[1]
        self.assertEqual(
            re.findall(r"name: '([^']+)'", parameter_block),
            ["BRANCH_TAG", "AGENT", "RELOAD_PIPELINE"],
        )
        self.assertIn("defaultValue: 'test'", parameter_block)


if __name__ == "__main__":
    unittest.main()
