"""V2 Jenkins 双服务发布助手；只有显式 --release 才会连接/修改集群。

仅使用 Python 标准库和 Jenkins withKubeConfig 提供的 kubectl 上下文。
不保存 Secret、现网完整 Deployment 或明文 Token 到文件/构建日志。
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import re
import subprocess
import sys
from typing import Optional


OWNER = "deep-agent-jenkins-v2"
OWNER_KEY = "app.kubernetes.io/managed-by"
DB_KEYS = tuple("DEEP_AGENT_DBMS_" + name for name in (
    "NAME", "HOSTNAME", "PORT", "DATABASE", "USER", "PASSWORD",
))
TOKEN_KEY = "DEEP_AGENT_CHANNEL_INTERNAL_TOKEN"
SHARED_KEYS = (*DB_KEYS, TOKEN_KEY)

# 在旧 API 容器内只解析配置，不建连接、不执行迁移。只输出相等与否和监听端口。
RUNTIME_CHECK = """
import base64, json, sys
from sqlalchemy.engine import make_url
from deep_agent.db.url import get_database_url
from deepfos_platform.options import OPTION
from deepfos_platform.lib.cipher import maybe_decrypt_pwd
expected = {key: base64.b64decode(value).decode() for key, value in json.load(sys.stdin).items()}
url = make_url(get_database_url())
fields = {
    'DEEP_AGENT_DBMS_NAME': {'postgresql': 'postgres', 'mysql': 'mysql'}.get(url.get_backend_name()),
    'DEEP_AGENT_DBMS_HOSTNAME': url.host,
    'DEEP_AGENT_DBMS_PORT': str(url.port),
    'DEEP_AGENT_DBMS_DATABASE': url.database,
    'DEEP_AGENT_DBMS_USER': url.username,
}
OPTION.gunicorn.set_env_prefix('DEEP_AGENT_')
bind = OPTION.gunicorn.python_bind
port = int(bind.rsplit(':', 1)[-1] if bind else OPTION.gunicorn.python_port)
matches = all(expected.get(key) == value for key, value in fields.items())
matches = matches and maybe_decrypt_pwd(expected['DEEP_AGENT_DBMS_PASSWORD']) == url.password
print(json.dumps({'database_matches': matches, 'api_port': port}))
"""


def health_code(url: str, *, authenticated: bool = False) -> str:
    # Token 在目标容器进程内读取，不进入 Pod httpHeaders 或 kubectl 参数。
    return (
        "import json,os,urllib.request;"
        f"url={url!r};"
        + (f"headers={{'Authorization':'Bearer '+os.environ[{TOKEN_KEY!r}]}};"
           if authenticated else "headers={};")
        + "opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));"
        "request=urllib.request.Request(url,headers=headers);"
        "body=json.load(opener.open(request,timeout=4));"
        "assert body.get('status')=='ok';"
        + ("assert body.get('database_revision')=='002';" if authenticated else "")
    )


def probe(port: int, *, authenticated: bool = False) -> dict:
    return {
        "exec": {"command": ["python", "-c", health_code(
            f"http://127.0.0.1:{port}/health", authenticated=authenticated,
        )]},
        "periodSeconds": 5, "timeoutSeconds": 6, "failureThreshold": 3,
    }


def shared_env(secret: str) -> list[dict]:
    return [{"name": key, "valueFrom": {"secretKeyRef": {
        "name": secret, "key": key,
    }}} for key in SHARED_KEYS]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_secret(secret: dict) -> None:
    data = secret.get("data", {})
    for key in SHARED_KEYS:
        require(key in data, f"共享 Secret 缺少 {key}")
        try:
            value = base64.b64decode(data[key], validate=True).decode()
        except (ValueError, UnicodeError):
            raise ValueError(f"共享 Secret 的 {key} 编码无效") from None
        require(bool(value.strip()), f"共享 Secret 的 {key} 不能为空")
        if key == "DEEP_AGENT_DBMS_NAME":
            require(value in ("mysql", "postgres"), "V2 仅支持共享 MySQL/PostgreSQL，不支持各 Pod 独立 SQLite")
        if key == "DEEP_AGENT_DBMS_PORT":
            require(value.isdecimal() and 0 < int(value) < 65536, "数据库端口无效")


def owned(resource: dict, expected: str) -> None:
    if resource:
        require(resource.get("metadata", {}).get("labels", {}).get(OWNER_KEY) == OWNER,
                f"{expected} 已存在且不属于 V2；请人工确认资源名称，拒绝覆盖")


def manifests(api: dict, *, name: str, container: str, namespace: str,
              image: str, secret: str, api_port: int) -> tuple[dict, dict, list[dict]]:
    """纯函数：保留 API 其他配置；网关独立 Pod，不复制 API 挂载/注册/副进程。"""
    spec = api["spec"]
    require(spec.get("replicas", 1) == 1, "API 必须先确认为单副本")
    require(not spec.get("paused"), "API Deployment 已暂停，请先人工处理")
    selector = spec["selector"]
    require(bool(selector.get("matchLabels")) and not selector.get("matchExpressions"),
            "V2 要求 API 使用非空 matchLabels selector")
    pod_spec = spec["template"]["spec"]
    containers = copy.deepcopy(pod_spec["containers"])
    targets = [c for c in containers if c["name"] == container]
    require(len(targets) == 1, "找不到指定 API 容器")
    target = targets[0]
    replacements = shared_env(secret) + [
        {"name": "DEEP_AGENT_LOAD_DOTENV", "value": "false"},
        {"name": "DEEP_AGENT_CHANNEL_GATEWAY_URL", "value": f"http://{name}-channel-gateway:8010"},
        {"name": "DEEP_AGENT_PYTHON_BIND", "value": f"0.0.0.0:{api_port}"},
    ]
    replaced_keys = {item["name"] for item in replacements}
    target["env"] = [item for item in target.get("env", []) if item["name"] not in replaced_keys] + replacements
    target.update(image=image, command=["gunicorn"], args=[
        "-c", "deep_agent/gunicorn.conf.py", "deep_agent.main:app",
    ])
    target["readinessProbe"] = probe(api_port)
    target["startupProbe"] = {**probe(api_port), "failureThreshold": 120}
    api_patch = {"spec": {
        "strategy": {"type": "Recreate", "rollingUpdate": None},
        "template": {"spec": {"containers": containers}},
    }}
    gateway_name = f"{name}-channel-gateway"
    labels = {OWNER_KEY: OWNER, "app.kubernetes.io/name": gateway_name}
    # 新标签不能命中 API 的 selector，否则既有 API Service 会混入 Gateway。
    require(not all(labels.get(k) == v for k, v in selector["matchLabels"].items()),
            "API selector 过宽，可能选中 Gateway；请先收窄 API 标签")
    gateway_probe = probe(8010, authenticated=True)
    gateway_pod = {
        "automountServiceAccountToken": False,
        "terminationGracePeriodSeconds": 60,
        "imagePullSecrets": copy.deepcopy(pod_spec.get("imagePullSecrets", [])),
        "containers": [{
            "name": "channel-gateway", "image": image,
            "command": ["python", "-m", "deep_agent.channel_gateway"],
            "args": ["--host", "0.0.0.0", "--port", "8010"],
            "ports": [{"name": "http", "containerPort": 8010}],
            "envFrom": [{"secretRef": {"name": secret}}],
            "env": shared_env(secret) + [
                {"name": "DEEP_AGENT_LOAD_DOTENV", "value": "false"},
                {"name": "DEEP_AGENT_CHANNEL_API_URL", "value": f"http://{name}-channel-api:{api_port}"},
            ],
            "resources": {"requests": {"cpu": "100m", "memory": "256Mi"},
                          "limits": {"cpu": "1", "memory": "1Gi"}},
            "startupProbe": {**gateway_probe, "failureThreshold": 120},
            "readinessProbe": gateway_probe,
            "livenessProbe": {**gateway_probe, "periodSeconds": 15, "failureThreshold": 4},
        }],
    }
    # 沿用现有调度约束；不照搬 API 的 PVC、initContainers、ServiceAccount 或业务 sidecar。
    for key in ("nodeSelector", "tolerations", "affinity"):
        if key in pod_spec:
            gateway_pod[key] = copy.deepcopy(pod_spec[key])
    gateway = {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": gateway_name, "namespace": namespace, "labels": labels},
        "spec": {"replicas": 1, "strategy": {"type": "Recreate"},
                 "selector": {"matchLabels": {"app.kubernetes.io/name": gateway_name}},
                 "template": {"metadata": {"labels": labels}, "spec": gateway_pod}},
    }
    services = []
    for service_name, service_selector, port in (
        (f"{name}-channel-api", selector["matchLabels"], api_port),
        (gateway_name, {"app.kubernetes.io/name": gateway_name}, 8010),
    ):
        services.append({
            "apiVersion": "v1", "kind": "Service",
            "metadata": {"name": service_name, "namespace": namespace, "labels": {OWNER_KEY: OWNER}},
            "spec": {"type": "ClusterIP", "selector": service_selector,
                     "ports": [{"name": "http", "port": port, "targetPort": port}]},
        })
    return api_patch, gateway, services


class Kubectl:
    def __init__(self, namespace: str):
        self.namespace = namespace

    def run(self, *args: str, payload: Optional[dict] = None) -> str:
        request_timeout = "0" if args[0] in ("rollout", "wait") else "20s"
        result = subprocess.run(
            ["kubectl", "--namespace", self.namespace, f"--request-timeout={request_timeout}", *args],
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True, text=True, timeout=660,
        )
        # kubectl 失败可能回显完整 patch。不要将 stderr 或 CalledProcessError 原样暴露。
        if result.returncode:
            raise RuntimeError(f"kubectl {args[0]} {args[1] if len(args) > 1 else ''} 失败；请运维在受控终端检查 RBAC、Pod 状态或事件")
        return result.stdout

    def get(self, kind: str, name: Optional[str] = None) -> dict:
        args = ["get", kind]
        if name:
            args += [name, "--ignore-not-found"]
        output = self.run(*args, "-o", "json")
        return json.loads(output) if output.strip() else {}


def release(kube: Kubectl, *, name: str, container: str, namespace: str,
            image: str, secret: str, api_port: int) -> None:
    api = kube.get("deployment", name)
    require(bool(api), "API Deployment 不存在；V2 沿用已部署主服务，不创建新的主服务环境")
    runtime_secret = kube.get("secret", secret)
    validate_secret(runtime_secret)
    runtime_check = json.loads(kube.run(
        "exec", "-i", f"deployment/{name}", "-c", container, "--", "python", "-c", RUNTIME_CHECK,
        payload={key: runtime_secret["data"][key] for key in DB_KEYS},
    ))
    require(runtime_check.get("database_matches"), "共享 Secret 与旧 API 数据库配置不一致，拒绝切库")
    require(runtime_check.get("api_port") == api_port, "API_PORT 与旧 API 配置端口不一致，拒绝改变既有路由")
    gateway_name = f"{name}-channel-gateway"
    old_gateway = kube.get("deployment", gateway_name)
    owned(old_gateway, gateway_name)
    if old_gateway:
        require(old_gateway["spec"].get("replicas", 1) <= 1, "Gateway 不能多副本")
        require(not old_gateway["spec"].get("paused"), "Gateway Deployment 已暂停")
    for hpa in kube.get("hpa").get("items", []):
        require(hpa["spec"]["scaleTargetRef"]["name"] not in (name, gateway_name),
                "API/Gateway 存在 HPA，请先人工关闭自动扩缩容")
    # ServiceAccount 可能在 Pod 准入时注入拉取凭据，不能只看 Deployment 模板。
    pod_spec = api["spec"]["template"]["spec"]
    service_account = kube.get("serviceaccount", pod_spec.get("serviceAccountName", "default"))
    require(bool(service_account), "API 使用的 ServiceAccount 不存在")
    pull_secrets = pod_spec.get("imagePullSecrets", []) or service_account.get("imagePullSecrets", [])
    pod_spec["imagePullSecrets"] = copy.deepcopy(pull_secrets)
    api_patch, gateway, services = manifests(
        api, name=name, container=container, namespace=namespace,
        image=image, secret=secret, api_port=api_port,
    )
    for service in services:
        owned(kube.get("service", service["metadata"]["name"]), service["metadata"]["name"])
    # 所有 server dry-run 在停服务前完成；只是准入检查，不代表已部署或网络可达。
    for resource in [*services, gateway]:
        kube.run("apply", "--dry-run=server", "-f", "-", payload=resource)
    kube.run("patch", "deployment", name, "--type=merge", "--dry-run=server", "--patch-file=/dev/stdin", payload=api_patch)
    if old_gateway:
        selector = old_gateway["spec"]["selector"]
        require(not selector.get("matchExpressions") and bool(selector.get("matchLabels")),
                "旧 Gateway selector 不能安全用于等待退出")
        label = ",".join(f"{k}={v}" for k, v in selector["matchLabels"].items())
        print("停止旧渠道网关，等待 Pod 完全退出…", flush=True)
        kube.run("scale", f"deployment/{gateway_name}", "--replicas=0")
        # scale 后先等控制器观察并收敛，再等所有旧 Pod（含 Terminating）消失。
        kube.run("rollout", "status", f"deployment/{gateway_name}", "--timeout=300s")
        remaining = json.loads(kube.run("get", "pods", "-l", label, "-o", "json"))
        if remaining.get("items"):
            kube.run("wait", "--for=delete", "pod", "-l", label, "--timeout=300s")
    for service in services:
        kube.run("apply", "-f", "-", payload=service)
    print("发布 API，等待迁移和启动完成…", flush=True)
    kube.run("patch", "deployment", name, "--type=merge", "--patch-file=/dev/stdin", payload=api_patch)
    kube.run("rollout", "status", f"deployment/{name}", "--timeout=600s")
    kube.run("exec", f"deployment/{name}", "-c", container, "--", "python", "-c", health_code(f"http://127.0.0.1:{api_port}/health"))
    print("发布渠道网关，等待就绪…", flush=True)
    kube.run("apply", "-f", "-", payload=gateway)
    kube.run("rollout", "status", f"deployment/{gateway_name}", "--timeout=600s")
    # 验证两个方向的 Service DNS/路由；不发送企微消息，不触发真实 Bot 测试。
    kube.run("exec", f"deployment/{name}", "-c", container, "--", "python", "-c", health_code(f"http://{gateway_name}:8010/health", authenticated=True))
    kube.run("exec", f"deployment/{gateway_name}", "-c", "channel-gateway", "--", "python", "-c", health_code(f"http://{name}-channel-api:{api_port}/health"))
    for deployment in (name, gateway_name):
        current = kube.get("deployment", deployment)
        require(current["spec"].get("replicas") == 1 and current.get("status", {}).get("readyReplicas") == 1,
                f"{deployment} 未达到 1/1 Ready")
        target_name = container if deployment == name else "channel-gateway"
        current_container = next(c for c in current["spec"]["template"]["spec"]["containers"] if c["name"] == target_name)
        require(current_container["image"] == image, f"{deployment} 镜像与本次构建不一致")
    print("API + Channel Gateway 均已就绪，内部双向健康检查通过。", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", action="store_true", help="已确认备份、目标数据库/端口、维护窗口和 schema 兼容后执行")
    for key in ("namespace", "name", "container", "image", "secret"):
        parser.add_argument(f"--{key}", required=True)
    parser.add_argument("--api-port", type=int, default=30098)
    args = parser.parse_args()
    require(args.release, "未提供 --release；未连接集群")
    for key in ("namespace", "name", "container", "secret"):
        value = getattr(args, key)
        require(len(value) <= 63 and bool(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", value)), f"{key} 不是有效的 Kubernetes 名称")
    require(len(args.name) <= 47, "API 名称过长，无法添加 -channel-gateway 后缀")
    require(0 < args.api_port < 65536, "API 端口无效")
    require(bool(re.fullmatch(r"[a-zA-Z0-9./:_@-]+", args.image)), "镜像引用无效")
    options = vars(args)
    options.pop("release")
    release(Kubectl(args.namespace), **options)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"发布中止：{exc if not isinstance(exc, subprocess.TimeoutExpired) else 'kubectl 超时'}。不自动回滚数据库或恢复旧网关，请人工确认。", file=sys.stderr)
        sys.exit(1)
