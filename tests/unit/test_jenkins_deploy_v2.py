"""仅检查 V2 发布编排；无集群、数据库、镜像构建或真实机器人连接。"""
from __future__ import annotations

import base64
import copy
import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import re
import subprocess
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("jenkins_deploy_v2", ROOT / "docs/guides/jenkins/deploy_v2.py")
DEPLOY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEPLOY)
OPTIONS = dict(name="deep-agent", container="deep-agent", namespace="smoke",
               image="registry.invalid/smoke/deep-agent:42-abcdef", secret="channel-runtime", api_port=30098)


def api_fixture():
    return {"spec": {
        "replicas": 1, "selector": {"matchLabels": {"app": "deep-agent"}},
        "template": {"spec": {"serviceAccountName": "api-runtime", "containers": [{
            "name": "deep-agent", "image": "old-image",
            "env": [{"name": "KEEP_SETTING", "value": "keep"},
                    {"name": "DEEP_AGENT_DBMS_USER", "value": "previous"}],
            "volumeMounts": [{"name": "existing", "mountPath": "/existing"}],
        }]}},
    }, "status": {"readyReplicas": 1}}


def secret_fixture():
    values = {key: "fixture-only" for key in DEPLOY.SHARED_KEYS}
    values.update(DEEP_AGENT_DBMS_NAME="mysql", DEEP_AGENT_DBMS_PORT="3306")
    return {"data": {key: base64.b64encode(value.encode()).decode() for key, value in values.items()}}


class FakeCluster:
    def __init__(self, existing_gateway=False):
        self.api = api_fixture()
        self.secret = secret_fixture()
        self.gateway = DEPLOY.manifests(self.api, **OPTIONS)[1] if existing_gateway else {}
        self.services = {}
        self.hpas = []
        self.events = []
        self.runtime = {"database_matches": True, "api_port": 30098}
        self.fail_api = False
        self.fail_dry_run = False

    def get(self, kind, name=None):
        if kind == "deployment":
            return copy.deepcopy(self.api if name == "deep-agent" else self.gateway)
        if kind == "secret":
            return copy.deepcopy(self.secret)
        if kind == "hpa":
            return {"items": self.hpas}
        if kind == "service":
            return self.services.get(name, {})
        if kind == "serviceaccount":
            return {"metadata": {"name": name}, "imagePullSecrets": [{"name": "harbor-pull"}]}
        raise AssertionError(kind)

    def run(self, *args, payload=None):
        self.events.append((args, copy.deepcopy(payload)))
        if "--dry-run=server" in args:
            if self.fail_dry_run:
                raise RuntimeError("admission rejected")
            return ""
        if args[0] == "scale":
            self.gateway["spec"]["replicas"] = 0
        elif args[0] == "patch":
            self.api["spec"]["template"]["spec"]["containers"] = payload["spec"]["template"]["spec"]["containers"]
        elif args[0] == "apply" and payload["kind"] == "Deployment":
            self.gateway = copy.deepcopy(payload)
            self.gateway["status"] = {"readyReplicas": 1}
        elif args[:3] == ("rollout", "status", "deployment/deep-agent") and self.fail_api:
            raise RuntimeError("API rollout timeout")
        elif args[0] == "exec" and DEPLOY.RUNTIME_CHECK in args:
            self.assert_preflight_payload(payload)
            return json.dumps(self.runtime)
        elif args[:2] == ("get", "pods"):
            return json.dumps({"items": [{"metadata": {"name": "old-gateway-pod"}}]})
        return ""

    @staticmethod
    def assert_preflight_payload(payload):
        assert set(payload) == set(DEPLOY.DB_KEYS)


class JenkinsDeploymentV2Smoke(unittest.TestCase):
    def run_release(self, cluster):
        with redirect_stdout(io.StringIO()):
            DEPLOY.release(cluster, **OPTIONS)

    def assert_no_mutation(self, cluster):
        self.assertFalse(any(args[0] in ("scale", "patch", "apply") and "--dry-run=server" not in args
                             for args, _ in cluster.events))

    def test_manifests_preserve_api_settings_and_isolate_gateway(self):
        original = api_fixture()
        api_patch, gateway, services = DEPLOY.manifests(original, **OPTIONS)
        target = api_patch["spec"]["template"]["spec"]["containers"][0]
        env = {item["name"]: item for item in target["env"]}
        self.assertEqual(env["KEEP_SETTING"]["value"], "keep")
        self.assertNotIn("value", env["DEEP_AGENT_DBMS_USER"])
        self.assertEqual(target["volumeMounts"], original["spec"]["template"]["spec"]["containers"][0]["volumeMounts"])
        self.assertEqual(api_patch["spec"]["strategy"], {"type": "Recreate", "rollingUpdate": None})
        self.assertEqual(gateway["spec"]["replicas"], 1)
        pod = gateway["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertNotIn("volumes", pod)
        self.assertEqual(pod["containers"][0]["command"], ["python", "-m", "deep_agent.channel_gateway"])
        self.assertEqual(target["image"], pod["containers"][0]["image"])
        self.assertNotEqual(services[0]["spec"]["selector"], services[1]["spec"]["selector"])
        for container in (target, pod["containers"][0]):
            keys = {item["name"]: item for item in container["env"]}
            for key in DEPLOY.SHARED_KEYS:
                self.assertEqual(keys[key]["valueFrom"]["secretKeyRef"], {"name": "channel-runtime", "key": key})
        self.assertEqual(original, api_fixture())

    def test_first_release_starts_both_and_inherits_pull_credentials(self):
        cluster = FakeCluster()
        self.run_release(cluster)
        self.assertFalse(any(args[0] == "scale" for args, _ in cluster.events))
        self.assertEqual(cluster.gateway["spec"]["template"]["spec"]["imagePullSecrets"], [{"name": "harbor-pull"}])
        operations = [args for args, _ in cluster.events]
        api_ready = operations.index(("rollout", "status", "deployment/deep-agent", "--timeout=600s"))
        gateway_start = next(i for i, (args, payload) in enumerate(cluster.events)
                             if args == ("apply", "-f", "-") and payload["kind"] == "Deployment")
        self.assertLess(api_ready, gateway_start)
        self.assertTrue(any("http://deep-agent-channel-gateway:8010/health" in " ".join(args) for args in operations))
        self.assertTrue(any("http://deep-agent-channel-api:30098/health" in " ".join(args) for args in operations))

    def test_repeat_release_waits_for_old_gateway_deletion_before_api_update(self):
        cluster = FakeCluster(existing_gateway=True)
        self.run_release(cluster)
        operations = [args for args, _ in cluster.events]
        scale = next(i for i, args in enumerate(operations) if args[0] == "scale")
        deletion = next(i for i, args in enumerate(operations) if args[0] == "wait" and "--for=delete" in args)
        api_update = next(i for i, args in enumerate(operations) if args[0] == "patch" and "--dry-run=server" not in args)
        self.assertLess(scale, deletion)
        self.assertLess(deletion, api_update)

    def test_invalid_config_stops_before_mutation(self):
        for scenario in ("missing_secret", "sqlite", "different_database", "wrong_port", "hpa", "multiple_api", "name_collision", "dry_run"):
            with self.subTest(scenario=scenario):
                cluster = FakeCluster(existing_gateway=True)
                if scenario == "missing_secret":
                    cluster.secret["data"].pop(DEPLOY.TOKEN_KEY)
                elif scenario == "sqlite":
                    cluster.secret["data"]["DEEP_AGENT_DBMS_NAME"] = base64.b64encode(b"sqlite").decode()
                elif scenario == "different_database":
                    cluster.runtime["database_matches"] = False
                elif scenario == "wrong_port":
                    cluster.runtime["api_port"] = 8000
                elif scenario == "hpa":
                    cluster.hpas = [{"spec": {"scaleTargetRef": {"name": "deep-agent"}}}]
                elif scenario == "multiple_api":
                    cluster.api["spec"]["replicas"] = 2
                elif scenario == "name_collision":
                    cluster.gateway["metadata"]["labels"] = {}
                elif scenario == "dry_run":
                    cluster.fail_dry_run = True
                with self.assertRaises((ValueError, RuntimeError)):
                    self.run_release(cluster)
                self.assert_no_mutation(cluster)

    def test_api_failure_does_not_start_or_restore_gateway(self):
        cluster = FakeCluster(existing_gateway=True)
        cluster.fail_api = True
        with self.assertRaises(RuntimeError):
            self.run_release(cluster)
        self.assertEqual(cluster.gateway["spec"]["replicas"], 0)
        self.assertFalse(any(args == ("apply", "-f", "-") and payload["kind"] == "Deployment"
                             for args, payload in cluster.events))

    def test_health_programs_compile_without_plaintext_credentials(self):
        compile(DEPLOY.RUNTIME_CHECK, "runtime-preflight", "exec")
        for authenticated in (False, True):
            code = DEPLOY.health_code("http://127.0.0.1:8010/health", authenticated=authenticated)
            compile(code, "health-probe", "exec")
            self.assertIn("ProxyHandler({})", code)
            self.assertNotIn("fixture-only", code)
        gateway_probe = DEPLOY.probe(8010, authenticated=True)
        self.assertNotIn("httpHeaders", json.dumps(gateway_probe))

    def test_kubectl_failure_does_not_print_server_secret_response(self):
        with patch.object(DEPLOY.subprocess, "run") as run:
            run.return_value.returncode = 1
            run.return_value.stderr = "SENSITIVE_SERVER_RESPONSE"
            with self.assertRaises(RuntimeError) as caught:
                DEPLOY.Kubectl("smoke").run("patch", "deployment", "deep-agent", payload={"sensitive": "value"})
        self.assertNotIn("SENSITIVE_SERVER_RESPONSE", str(caught.exception))

    def test_rollout_watch_uses_its_explicit_timeout(self):
        with patch.object(DEPLOY.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "ready"
            DEPLOY.Kubectl("smoke").run("rollout", "status", "deployment/deep-agent", "--timeout=600s")
            self.assertIn("--request-timeout=0", run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs["timeout"], 660)

    def test_missing_release_confirmation_never_connects(self):
        argv = ["deploy_v2.py", "--name", "deep-agent", "--container", "deep-agent", "--namespace", "smoke", "--image", OPTIONS["image"], "--secret", "channel-runtime"]
        with patch.object(DEPLOY.sys, "argv", argv), patch.object(DEPLOY.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                DEPLOY.main()
            run.assert_not_called()

    def test_jenkins_embedded_shell_syntax(self):
        pipeline = (ROOT / "docs/guides/jenkins/Jenkinsfile.v2").read_text()
        scripts = re.findall(r"sh '''(.*?)'''", pipeline, flags=re.DOTALL)
        self.assertEqual(len(scripts), 4)
        for script in scripts:
            # -n 仅解析语法，不执行 git、Docker、kubectl 或发布助手。
            result = subprocess.run(["/bin/sh", "-n"], input=script, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("sh 'env'", pipeline)
        self.assertIn("disableConcurrentBuilds()", pipeline)
        self.assertIn("--release", pipeline)
        self.assertIn("CONFIRM_RELEASE", pipeline)
        self.assertLess(pipeline.index("stage('漏洞检测')"), pipeline.index("stage('RELEASE API + Channel Gateway')"))
        dockerfile = re.search(r"writeFile file: 'Dockerfile', text: '''(.*?)'''", pipeline, flags=re.DOTALL)[1]
        dockerfile = dockerfile.replace("\\\\", "\\")  # Groovy 单引号字符串的反斜杠解码
        self.assertIn("WORKDIR /deep-agent", dockerfile)
        self.assertIn("&& \\\n", dockerfile)
        self.assertIn("deep_agent.channel_gateway.__main__", dockerfile)


if __name__ == "__main__":
    unittest.main()
