from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / "docs/guides/jenkins/Jenkinsfile.diagnose.v1"

# Only simulated data. The stub rejects any non-read-only command or other namespace.
KUBECTL_STUB = r'''
import json, os, sys
args = sys.argv[1:]
with open(os.environ["DIAG_CALLS"], "a") as stream:
    stream.write(json.dumps(args) + "\n")
assert args[:3] == ["--request-timeout=15s", "-n", "test"], args
args = args[3:]
assert args[0] in ("get", "logs"), args
mode = os.environ.get("DIAG_CASE", "")
if args[0] == "logs":
    assert args[args.index("-c") + 1] == "deep-agent"
    assert args[1] in ("deep-agent-old-pod", "deep-agent-new-pod"), args
    if "--previous" in args and args[1] == "deep-agent-old-pod":
        print("previous terminated container not found", file=sys.stderr)
        sys.exit(1)
    print("LOG " + args[1])
    print("RuntimeError: API exited during startup with code 3")
    print("FileNotFoundError: /tmp/deep-agent-channel-runtime/token")
    print("DEEP_AGENT_CHANNEL_INTERNAL_TOKEN=fixture-secret")
    print('{"password": "fixture-password"}')
    print("postgresql://fixture-user:fixture-pass@db/test")
    print("Set option DBMS_HOST = fixture-host")
    sys.exit(0)
target = args[1]
if target == "deployment/deep-agent":
    if mode == "missing":
        print("deployments.apps deep-agent not found", file=sys.stderr)
        sys.exit(1)
    if args[-1] == "jsonpath={.metadata.uid}":
        print("deploy-uid")
    else:
        print("deep-agent replicas=1 strategy=RollingUpdate ready=0 updated=1")
elif target == "replicasets":
    if mode == "denied":
        print("Forbidden: cannot list replicasets", file=sys.stderr)
        sys.exit(1)
    if mode != "empty":
        print("rs-old deep-agent-old deploy-uid")
        print("rs-new deep-agent-new deploy-uid")
    print("rs-unrelated deep-agent-lookalike other-deploy-uid")
elif target == "pods":
    if mode != "no-pods":
        print("deep-agent-old-pod pod-old rs-old")
        print("deep-agent-new-pod pod-new rs-new")
    print("unrelated-pod other-pod rs-unrelated")
elif target == "events":
    assert args[args.index("--field-selector") + 1] in (
        "involvedObject.uid=deploy-uid", "involvedObject.uid=rs-old",
        "involvedObject.uid=rs-new", "involvedObject.uid=pod-old",
        "involvedObject.uid=pod-new",
    ), args
    if mode == "events-denied":
        print("Forbidden: cannot list events", file=sys.stderr)
        sys.exit(1)
    print("Warning BackOff Back-off restarting failed container")
elif target.startswith("replicaset/"):
    assert target in ("replicaset/deep-agent-old", "replicaset/deep-agent-new")
    print("ReplicaSet " + target)
elif target.startswith("pod/"):
    assert target in ("pod/deep-agent-old-pod", "pod/deep-agent-new-pod")
    print("CrashLoopBackOff restarts=3 exitCode=3")
else:
    raise AssertionError(args)
'''


class JenkinsDiagnosticsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipeline = PIPELINE.read_text()
        blocks = re.findall(r"sh '''(.*?)'''", cls.pipeline, flags=re.DOTALL)
        if len(blocks) != 1:
            raise AssertionError("Expected one self-contained diagnostic shell block")
        source = blocks[0]
        # Groovy triple-single-quoted strings consume escapes too. Require doubled
        # backslashes, then run the actual string Jenkins will pass to /bin/sh.
        if re.search(r"(?<!\\)(?:\\\\)*\\(?!\\)", source):
            raise AssertionError("Unescaped backslash in Groovy shell block")
        cls.script = source.replace("\\\\", "\\")

    def run_diagnostics(self, case=""):
        with tempfile.TemporaryDirectory(
            prefix="TMP_to_delete-jenkins-diagnostics-", dir=ROOT
        ) as directory:
            temporary = Path(directory)
            calls = temporary / "calls.jsonl"
            stub = temporary / "kubectl"
            stub.write_text(f"#!{sys.executable}\n{KUBECTL_STUB}")
            stub.chmod(0o700)
            result = subprocess.run(
                ["/bin/sh"], input=self.script, text=True, capture_output=True,
                timeout=15,
                env={
                    **os.environ, "PATH": f"{temporary}:{os.environ['PATH']}",
                    "DIAG_CALLS": str(calls), "DIAG_CASE": case,
                },
            )
            commands = [json.loads(line) for line in calls.read_text().splitlines()]
        return result, commands

    def test_shell_syntax_and_no_release_dependencies(self):
        result = subprocess.run(
            ["/bin/sh", "-n"], input=self.script, text=True, capture_output=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skipDefaultCheckout(true)", self.pipeline)
        for forbidden in ("checkout(", "docker.", "gitParameter", "params.", "python",
                          "kubectl exec", "kubectl patch", "kubectl apply"):
            self.assertNotIn(forbidden, self.pipeline)

    def test_exact_owner_scoping_current_previous_logs_and_redaction(self):
        result, commands = self.run_diagnostics()
        self.assertEqual(result.returncode, 0, result.stderr)
        logs = [command[3:] for command in commands if command[3] == "logs"]
        self.assertEqual(len(logs), 4)
        self.assertEqual({cmd[1] for cmd in logs}, {
            "deep-agent-old-pod", "deep-agent-new-pod",
        })
        self.assertEqual(sum("--previous" in cmd for cmd in logs), 2)
        for cmd in logs:
            self.assertIn("--tail=200", cmd)
            self.assertIn("--limit-bytes=65536", cmd)
        self.assertIn("API exited during startup with code 3", result.stdout)
        self.assertIn("/tmp/deep-agent-channel-runtime/token", result.stdout)
        self.assertIn("读取失败 exit=1", result.stdout)
        self.assertIn("诊断收集结束", result.stdout)
        for secret in ("fixture-secret", "fixture-password", "fixture-pass", "fixture-host"):
            self.assertNotIn(secret, result.stdout)
        self.assertNotIn("unrelated-pod", result.stdout)

    def test_missing_deployment_or_list_permission_stops_with_real_error(self):
        for case, error in (("missing", "not found"), ("denied", "Forbidden")):
            with self.subTest(case=case):
                result, _ = self.run_diagnostics(case)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)
                self.assertNotIn("诊断收集结束", result.stdout)

    def test_no_replicaset_or_no_pod_is_explicit_and_reads_no_logs(self):
        for case, message in (("empty", "没有找到"), ("no-pods", "当前没有 Pod")):
            with self.subTest(case=case):
                result, commands = self.run_diagnostics(case)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(message, result.stdout)
                self.assertFalse(any(command[3] == "logs" for command in commands))

    def test_event_permission_failure_does_not_hide_container_logs(self):
        result, _ = self.run_diagnostics("events-denied")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Forbidden: cannot list events", result.stdout)
        self.assertIn("LOG deep-agent-new-pod", result.stdout)


if __name__ == "__main__":
    unittest.main()
