from __future__ import annotations

import json
import os
import re
import signal
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

    def test_local_plan_keeps_identity_in_profile_and_runs_local_dev(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-combined-runtime-") as directory:
            profile = Path(directory) / "sqlite.env"
            profile.write_text(
                "\n".join([
                    "DEEP_AGENT_LOCAL_USER=local-user",
                    "DEEP_AGENT_LOCAL_TOKEN=local-platform-token",
                    "DEEP_AGENT_SQLITE_PATH=/tmp/local.db",
                ]),
                encoding="utf-8",
            )
            profile.chmod(0o600)
            with patch.object(runtime.secrets, "token_urlsafe", return_value="internal-token"):
                environ, command = runtime.runtime_plan(
                    local_config=profile,
                    local_api_port=8000,
                    environ={},
                )
        self.assertNotIn("DEEP_AGENT_LOCAL_USER", environ)
        self.assertNotIn("DEEP_AGENT_LOCAL_TOKEN", environ)
        self.assertEqual(environ[runtime.TOKEN_ENV], "internal-token")
        self.assertEqual(environ["DEEP_AGENT_SQLITE_PATH"], "/tmp/local.db")
        self.assertEqual(environ["DEEP_AGENT_CHANNEL_API_URL"], "http://127.0.0.1:8000")
        self.assertEqual(command[1:3], ("-m", "deep_agent.local_dev"))
        self.assertEqual(command[-4:], ("--config", str(profile.resolve()), "--port", "8000"))

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

    def test_check_requires_both_health_endpoints_without_revision_gate(self):
        environ = {
            runtime.TOKEN_ENV: "token",
            "DEEP_AGENT_PYTHON_PORT": "30098",
            "DEEP_AGENT_CHANNEL_PORT": "8010",
        }
        for revision in ("003", "future-compatible-fixture", None):
            with self.subTest(revision=revision), patch.object(runtime, "fetch_health", side_effect=[
                {"status": "ok"},
                {"status": "ok", "database_revision": revision},
            ]) as fetch:
                runtime.check_runtime(environ)
                self.assertEqual(fetch.call_args_list, [
                    call("http://127.0.0.1:30098/health"),
                    call("http://127.0.0.1:8010/health", token="token"),
                ])

    def test_check_still_propagates_unhealthy_gateway(self):
        with patch.object(runtime, "fetch_health", side_effect=[{"status": "ok"}, RuntimeError("gateway unhealthy")]):
            with self.assertRaisesRegex(RuntimeError, "gateway unhealthy"):
                runtime.check_runtime({runtime.TOKEN_ENV: "token"})

    def test_local_children_can_stay_in_devbox_process_group(self):
        process = Mock()
        process.poll.return_value = None
        runtime._signal_process(process, signal.SIGTERM, use_process_group=False)
        process.terminate.assert_called_once_with()
        process.kill.assert_not_called()

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


class JenkinsGatewayPipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipeline = (ROOT / "deploy/jenkins/Jenkinsfile.gateway").read_text()

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
        self.assertNotIn("kubectl set image", self.pipeline)
        self.assertIn('kubectl patch "deployment/$NAME" --type=strategic', self.pipeline)
        self.assertIn('"command":["python"]', self.pipeline)
        self.assertIn('"args":["-m","deep_agent.combined_runtime"]', self.pipeline)
        self.assertIn('kubectl rollout status "deployment/$NAME"', self.pipeline)
        self.assertIn("python -m deep_agent.combined_runtime --check", self.pipeline)

    def test_release_patch_targets_named_container_and_sets_runtime_entrypoint(self):
        release_script = re.findall(r"sh '''(.*?)'''", self.pipeline, flags=re.DOTALL)[-1]
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-jenkins-kubectl-") as directory:
            root = Path(directory)
            log = root / "kubectl.jsonl"
            kubectl = root / "kubectl"
            kubectl.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "with open(os.environ['KUBECTL_LOG'], 'a') as stream:\n"
                "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n",
                encoding="utf-8",
            )
            kubectl.chmod(0o755)
            environ = dict(os.environ)
            environ.update(
                {
                    "PATH": f"{root}:{environ['PATH']}",
                    "KUBECTL_LOG": str(log),
                    "NAME": "deep-agent",
                    "RELEASE_IMAGE": "harbor.deepfos.com/test/deep-agent:123-abc",
                }
            )
            result = subprocess.run(
                ["/bin/sh"],
                input=release_script,
                text=True,
                capture_output=True,
                env=environ,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        patch_call = calls[0]
        self.assertEqual(patch_call[:3], ["patch", "deployment/deep-agent", "--type=strategic"])
        payload = json.loads(patch_call[patch_call.index("--patch") + 1])
        self.assertEqual(
            payload["spec"]["template"]["spec"]["containers"],
            [
                {
                    "name": "deep-agent",
                    "image": "harbor.deepfos.com/test/deep-agent:123-abc",
                    "command": ["python"],
                    "args": ["-m", "deep_agent.combined_runtime"],
                    "workingDir": "/deep-agent",
                    "env": [{"name": "PYTHONPATH", "value": "/deep-agent/src", "valueFrom": None}],
                }
            ],
        )

    def test_gateway_image_uses_canonical_source_and_retains_original_archive(self):
        dockerfile = re.search(
            r"writeFile file: 'Dockerfile', text: '''(.*?)'''", self.pipeline, flags=re.DOTALL
        )[1]
        self.assertIn('CMD ["python", "-m", "deep_agent.combined_runtime"]', dockerfile)
        self.assertIn("WORKDIR /deep-agent", dockerfile)
        self.assertIn("ENV PYTHONPATH=/deep-agent/src", dockerfile)
        v1 = (ROOT / "docs/archive/jenkins/Jenkinsfile.original").read_text()
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

    def test_restoring_release_after_diagnostics_has_first_build_defaults(self):
        self.assertIn("params.AGENT ?: 'java21-mvn3'", self.pipeline)
        self.assertIn("params.BRANCH_TAG ?: 'test'", self.pipeline)
        self.assertEqual(self.pipeline.count("when { expression { params.RELOAD_PIPELINE != 'true' } }"), 4)
        self.assertNotIn("when { environment name: 'RELOAD_PIPELINE'", self.pipeline)

    def test_image_validates_gateway_entrypoint_with_its_installed_uvicorn(self):
        self.assertIn("python -m unittest tests.unit.test_channel_gateway_entrypoint -v", self.pipeline)


if __name__ == "__main__":
    unittest.main()
