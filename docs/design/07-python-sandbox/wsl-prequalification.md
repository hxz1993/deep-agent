# WSL go-judge 预演记录（非生产签字）

面向研发本地预习。**`selection_eligible=true` 仅表示机制在 WSL 上跑通**，正式选型仍须在目标 K8s dev sidecar 重跑探针并归档 JSON。见 [`implementation-plan.md`](implementation-plan.md) · [`ops-kubernetes-sidecar.md`](ops-kubernetes-sidecar.md)。

---

## 1. 结果摘要（2026-08-02）

| 项 | 值 |
| --- | --- |
| 环境 | WSL2 · cgroup v2 · systemd 已启用 · kernel `6.18.33.2-microsoft-standard-WSL2` |
| 运行时 | go-judge **v1.12.2**（自编 seccomp 二进制）· `127.0.0.1:5050` 宿主直跑 |
| 探针结论 | **`selection_eligible=true`** |
| 报告 | `/tmp/go-judge-wsl.json`（副本 `scripts/sandbox_probe/wsl_go_judge/go-judge-wsl-report.json`） |
| 一键复跑 | `bash scripts/sandbox_probe/wsl_go_judge/run_host_probe.sh` |

### 检查项

| 类别 | 结果 |
| --- | --- |
| 结构性 mandatory（8 项） | 全部 **pass**（含 `python_profile`、`per_request_cgroup`） |
| 攻击矩阵 | 12 **pass**；`read_service_account` **skip**（本机无 K8s SA 挂载，预期） |

`per_request_cgroup` 备注：客人 `/proc/self/cgroup` 仍为 `0::/`；探针通过宿主 `system.slice/gojudge.scope/containers` 观察到两次并发请求的独立 transient cgroup 目录（见报告 `notes` 中 `per_request_cgroup_host_verify=...`）。

---

## 2. 一键脚本与产物

| 路径 | 用途 |
| --- | --- |
| `scripts/sandbox_probe/wsl_go_judge/run_host_probe.sh` | rsync site-packages → 生成 mount → 重启 go-judge → 跑探针 |
| `scripts/sandbox_probe/wsl_go_judge/go-judge` | pin 的自编 seccomp 二进制（gitignore，不入库） |
| `scripts/sandbox_probe/wsl_go_judge/mount.yaml` | 基础 mount（与上游 go-judge 默认对齐） |
| `scripts/sandbox_probe/wsl_go_judge/mount.wsl-host.overlay.yaml` | WSL 宿主 overlay（`/sandbox` tmpfs + site-packages bind） |
| `scripts/sandbox_probe/wsl_go_judge/seccomp.yaml` | seccomp 规则（socket/connect 用 **errno**，见 §3） |
| `scripts/sandbox_probe/wsl_go_judge/Dockerfile` | K8s sidecar 镜像草案（base `python3.11.9:debian12` + 镜像内 pip pydata） |
| `/tmp/deep-agent-go-judge-host/` | 宿主运行目录（二进制、mount、staging `site-packages/`） |

探针参数（脚本内置）：`--python python3.11`、`--env PYTHONPATH=/sandbox/site-packages`。

---

## 3. 技术纠正与踩坑

### 3.1 go-judge 二进制

| 问题 | 纠正 |
| --- | --- |
| GitHub Release 默认构建**不含** `seccomp` tag；只放 `seccomp.yaml` 会被**静默忽略** | 从源码 **v1.12.2**（commit `955585f050286993ead2b2dd11c107d529b6b508`）自编：`-tags seccomp,nomsgpack,grpcnotrace` |
| Jenkins / 节点无外网 | **禁止**运行时拉 GitHub/Docker Hub；二进制与 base 镜像须进 **Harbor** pin digest，仓库只 pin Dockerfile / mount / seccomp / sha256 |

### 3.2 seccomp.yaml

| 问题 | 纠正 |
| --- | --- |
| 对 `socket` / `connect` 使用 `action: kill_thread` | 会误杀正常 `print()` 等 syscall 链；改为 **`action: errno`** |

### 3.3 WSL 运行拓扑

| 方式 | 结论 |
| --- | --- |
| **宿主直跑** go-judge + WSL **systemd** | **可行**；可连 dbus，创建 `gojudge.scope` |
| Docker sidecar 挂宿主 `/run/dbus` | **失败**：`Process 1 is a manager process, refusing` |
| 无 systemd（PID 1 非 systemd） | cgroup transient unit 不可用；不能当作 sidecar 预习 |

重启注意：须 `pkill` + `fuser -k 5050/tcp` + `systemctl stop gojudge.scope`，否则易出现 `Unit gojudge.scope already exists` 或旧进程占口。

### 3.4 mount / python_profile

| 问题 | 纠正 |
| --- | --- |
| 从 `$HOME/.../venv/...` **直接 bind** 进 jail | go-judge prefork 报 `mount: permission denied` |
| bind 到 **只读 `/usr` 子路径** | 同上（`/usr` 已整体 ro bind） |
| bind 到 **`/w` 工作目录子路径** | 同上 |
| **可行方案（WSL 宿主）** | ① `rsync` venv site-packages → `$HOST_DIR/site-packages`（路径在 `/tmp`）；② overlay 增加 `tmpfs /sandbox`；③ bind `$HOST_DIR/site-packages` → `/sandbox/site-packages`；④ `PYTHONPATH=/sandbox/site-packages` + `python3.11` |
| **K8s sidecar（推荐）** | 在镜像内 `pip install` pydata（见 `Dockerfile`），**无需**宿主 `$HOME` bind |

### 3.5 探针与环境

| 问题 | 纠正 |
| --- | --- |
| 本机 HTTP 代理（如 `127.0.0.1:6789`）访问 `127.0.0.1:5050` | 返回 **502**；探针脚本与 `go_judge.py` 须清代理 / 使用无代理 urllib |
| 客人 cgroup 路径恒为 `0::/` | 不判 fail；若存在 `gojudge.scope/containers`，探针改做**宿主侧** transient cgroup 校验（`common.py`） |
| `read_service_account` skip | 无 `/var/run/secrets` 挂载时预期 skip，不拉低 `selection_eligible` |

---

## 4. 与 K8s 正式签字的差异

| 维度 | WSL 预演 | K8s dev sidecar（待做） |
| --- | --- | --- |
| 运行方式 | 宿主 sudo 直跑 | Pod 第二容器 · privileged / cap + cgroup 挂载 |
| pydata | rsync + bind `/sandbox` | 镜像内 pip（Dockerfile） |
| per_request_cgroup | 宿主路径校验 | 期望客人 cgroup 路径可区分，或同样宿主校验 |
| 签字效力 | **预习 only** | **选型签字** |

---

## 5. 参考命令

```bash
# 预演（仓库根）
bash scripts/sandbox_probe/wsl_go_judge/run_host_probe.sh

# 手动探针（go-judge 已监听时）
./venv/bin/python scripts/sandbox_probe/go_judge.py \
  --endpoint http://127.0.0.1:5050 \
  --python python3.11 \
  --env PYTHONPATH=/sandbox/site-packages \
  --binary scripts/sandbox_probe/wsl_go_judge/go-judge \
  --report /tmp/go-judge-wsl.json
```

Pin 元数据（与 `go_judge.py` 一致）：version `v1.12.2`，commit `955585f050286993ead2b2dd11c107d529b6b508`，license MIT。
