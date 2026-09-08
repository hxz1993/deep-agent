from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deep_agent import combined_runtime


ROOT = Path(__file__).resolve().parents[2]
PIPELINES = ROOT / "deploy/jenkins"


class JenkinsApiOnlyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipeline = (PIPELINES / "Jenkinsfile.api-only").read_text()
        cls.scripts = re.findall(r"sh '''(.*?)'''", cls.pipeline, flags=re.DOTALL)

    def test_original_is_an_unchanged_archive(self):
        self.assertEqual(
            hashlib.sha256((ROOT / "docs/archive/jenkins/Jenkinsfile.original").read_bytes()).hexdigest(),
            "367bbc78b25319878f8c235c77bc910ace77a2333360f7b0c01baf39683b6860",
        )

    def test_shell_syntax_and_familiar_build_parameters(self):
        self.assertEqual(len(self.scripts), 4)
        for script in self.scripts:
            result = subprocess.run(["/bin/sh", "-n"], input=script, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        parameters = re.search(r"parameters \{(.*?)\n    \}\n    stages", self.pipeline, re.S)[1]
        self.assertEqual(re.findall(r"name: '([^']+)'", parameters),
                         ["BRANCH_TAG", "AGENT", "RELOAD_PIPELINE"])
        self.assertIn("type: 'PT_BRANCH_TAG'", parameters)
        self.assertIn("params.AGENT ?: 'java21-mvn3'", self.pipeline)
        self.assertIn("params.BRANCH_TAG ?: 'test'", self.pipeline)
        self.assertEqual(self.pipeline.count("when { expression { params.RELOAD_PIPELINE != 'true' } }"), 4)
        self.assertNotIn("command -v python", self.pipeline)

    def run_release(self, fail_at=""):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-api-pipeline-", dir=ROOT) as directory:
            root = Path(directory)
            log = root / "calls.jsonl"
            stub = root / "kubectl"
            stub.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "with open(os.environ['CALLS'], 'a') as stream:\n"
                "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "sys.exit(7 if sys.argv[1] == os.environ['FAIL_AT'] else 0)\n"
            )
            stub.chmod(0o700)
            result = subprocess.run(
                ["/bin/sh"], input=self.scripts[-1], text=True, capture_output=True, timeout=10,
                env={**os.environ, "PATH": f"{root}:{os.environ['PATH']}", "CALLS": str(log),
                     "FAIL_AT": fail_at, "NAME": "deep-agent",
                     "RELEASE_IMAGE": "registry.test/test/deep-agent:123-abc"},
            )
            calls = [json.loads(line) for line in log.read_text().splitlines()]
        return result, calls

    def test_release_overrides_gateway_entrypoint_and_clears_gateway_url(self):
        result, calls = self.run_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[0] for call in calls], ["patch", "rollout", "exec"])
        command = calls[0]
        self.assertEqual(command[:3], ["patch", "deployment/deep-agent", "--type=strategic"])
        payload = json.loads(command[command.index("--patch") + 1])
        self.assertEqual(payload, {"spec": {"template": {"spec": {"containers": [{
            "name": "deep-agent", "image": "registry.test/test/deep-agent:123-abc",
            "command": ["gunicorn"],
            "args": ["-c", "deploy/gunicorn.conf.py", "deep_agent.main:app"],
            "workingDir": "/deep-agent",
            "env": [
                {"name": "PYTHONPATH", "value": "/deep-agent/src", "valueFrom": None},
                {"name": "DEEP_AGENT_CHANNEL_GATEWAY_URL", "value": "", "valueFrom": None},
            ],
        }]}}}})
        self.assertEqual(calls[1], ["rollout", "status", "deployment/deep-agent", "--timeout=600s"])
        self.assertEqual(calls[2][:6], ["exec", "deployment/deep-agent", "-c", "deep-agent", "--", "python"])
        self.assertNotIn("--check", calls[2])  # Never require a gateway in API-only mode.

    def test_each_release_failure_stops_the_pipeline(self):
        for stage, count in (("patch", 1), ("rollout", 2), ("exec", 3)):
            with self.subTest(stage=stage):
                result, calls = self.run_release(stage)
                self.assertEqual(result.returncode, 7)
                self.assertEqual(len(calls), count)

    def test_api_health_probe_uses_configured_port_without_gateway_or_database_write(self):
        _, calls = self.run_release()
        code = calls[-1][-1]
        for env, port in (({}, 30098), ({"DEEP_AGENT_PYTHON_PORT": "31000"}, 31000),
                          ({"DEEP_AGENT_PYTHON_BIND": "0.0.0.0:32000"}, 32000)):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                with patch.object(combined_runtime, "fetch_health", return_value={"status": "ok"}) as fetch:
                    exec(code, {})
                    fetch.assert_called_once_with(f"http://127.0.0.1:{port}/health")
        with patch.object(combined_runtime, "fetch_health", side_effect=RuntimeError("unhealthy")):
            with self.assertRaisesRegex(RuntimeError, "unhealthy"):
                exec(code, {})

    def test_image_runs_api_only_and_switch_back_still_uses_combined_runtime(self):
        dockerfile = re.search(r"writeFile file: 'Dockerfile', text: '''(.*?)'''", self.pipeline, re.S)[1]
        self.assertIn('CMD ["gunicorn", "-c", "deploy/gunicorn.conf.py", "deep_agent.main:app"]', dockerfile)
        self.assertIn("WORKDIR /deep-agent", dockerfile)
        self.assertIn("ENV PYTHONPATH=/deep-agent/src", dockerfile)
        self.assertNotIn("deep_agent.channel_gateway", dockerfile)
        gateway = (PIPELINES / "Jenkinsfile.gateway").read_text()
        self.assertIn('"command":["python"],"args":["-m","deep_agent.combined_runtime"]', gateway)
        self.assertIn('CMD ["python", "-m", "deep_agent.combined_runtime"]', gateway)
        # Gateway supervisor overrides the blank URL left by the API-only release.
        env = combined_runtime.build_child_env({"DEEP_AGENT_CHANNEL_GATEWAY_URL": ""})
        self.assertEqual(env["DEEP_AGENT_CHANNEL_GATEWAY_URL"], "http://127.0.0.1:8010")
