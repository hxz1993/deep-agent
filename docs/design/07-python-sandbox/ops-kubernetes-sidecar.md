# Python Sandbox：go-judge Sidecar（运维）

面向运维 / Rancher / Jenkins。产品语义见 [`README.md`](README.md)；阶段任务见 [`implementation-plan.md`](implementation-plan.md)。

**进度：** 文档与探针脚本已就绪；WSL 宿主预演 `selection_eligible=true`（见 [`wsl-prequalification.md`](wsl-prequalification.md)）。**dev 双容器落地、K8s 节点探针归档、正式选型签字未完成**。test 产品验收环境在签字前保持 `subprocess-dev`、不加 sidecar。

---

## 1. 为何 Sidecar（而非塞进应用容器）

| 现状 `subprocess-dev` | 目标阶段 C |
| --- | --- |
| 应用进程内起子进程，`security_level=none` | 由 go-judge（或 NsJail）做每请求硬隔离 |
| 不可宣称跑不可信代码 | 资格通过后方可评估 |

**不合并进 deep-agent 容器的原因：**

1. 沙箱运行时通常需要 privileged / 额外 cap；应用容器应保持非特权。
2. 攻击面分离：应用漏洞 ≠ 扩大沙箱逃逸面。
3. 镜像与发布节奏分离（应用 vs runtime pin）。

同 Pod 第二容器，深 agent 经 `http://127.0.0.1:5050` 访问；**无需**为 go-judge 单独建 Service。

```text
Pod
├─ deep-agent（gunicorn :8080，非 privileged）
└─ go-judge（:5050，privileged 或探针确认的 cap + cgroup 挂载）
```

---

## 2. 环境策略：test vs dev

| | **test**（产品验收） | **dev**（沙箱专项） |
| --- | --- | --- |
| Workload | 现有 `deep-agent` | 新建 `deep-agent-sandbox` |
| 容器 | 仅应用 | 应用 + go-judge |
| Backend | `subprocess-dev` | B：sidecar 供探针；C：可切 go-judge |
| 探针 | **不跑** | 必跑并归档 JSON |
| Jenkins | 现有流水线；**须打入 sandbox venv**（§4） | 两条独立流水线（§3） |

签字前禁止：给 test Pod 加 sidecar、在 test 启用 `BACKEND=go-judge`、用探针失败阻塞产品发版。

---

## 3. Jenkins（dev）

与 test 流水线**并列新建**，勿改坏 test 行为。

| 流水线 | 职责 |
| --- | --- |
| `dev/go-judge` | 构建/推送 **pin 版本** go-judge 镜像（禁 `latest`）；镜像须含**自编 seccomp 二进制** + 镜像内 pydata（见 §3.1）；只滚 dev sidecar 容器 |
| `dev/deep-agent-sandbox` | 构建应用镜像（含 sandbox venv）+ 部署双容器 Deployment；Stage 跑探针并 `archiveArtifacts` |

草案 Dockerfile：`scripts/sandbox_probe/wsl_go_judge/Dockerfile`（base `harbor.deepfos.com/base/python3.11.9:debian12`）。

可选第三条：`dev/nsjail-qualify`（go-judge 未过时）。

### 3.1 go-judge 镜像要点（摘要）

| 项 | 要求 |
| --- | --- |
| 二进制来源 | **禁止**节点运行时从 GitHub 拉 Release（默认构建无 seccomp）；须 CI 编入镜像或 Harbor 制品 |
| 编译 | 源码 **v1.12.2**，`-tags seccomp,nomsgpack,grpcnotrace`；与 `go_judge.py` pin 一致 |
| pydata | 镜像内 `pip install numpy<2 pandas`（与 `scripts/sandbox_venv/requirements.txt` 对齐）；**不要**依赖运行时 bind 宿主 `$HOME` |
| seccomp.yaml | 禁网 syscall 用 `action: errno`（勿 `kill_thread`） |
| 探针 | `selection_eligible=true` 后再议阶段 C |

WSL 本地预习用宿主脚本 `run_host_probe.sh`（rsync site-packages 到 `/tmp` 再 bind）；**不等价**于本 sidecar 签字。

### 3.2 go-judge 源码与制品准备（GitHub → Harbor）

上游：[criyle/go-judge](https://github.com/criyle/go-judge)。**不要**直接把 GitHub Release 里的 `go-judge_linux_amd64` 塞进镜像——该产物默认**未带** `seccomp` build tag，即使挂载了 `seccomp.yaml` 也会被**静默忽略**，探针里 `network_blocked` / `seccomp_enforced` 会失真。

#### Pin 元数据（与探针对齐）

| 字段 | 值 |
| --- | --- |
| 版本 tag | `v1.12.2` |
| commit | `955585f050286993ead2b2dd11c107d529b6b508` |
| license | MIT |
| build tags | `seccomp,nomsgpack,grpcnotrace` |
| 探针 pin 来源 | `scripts/sandbox_probe/go_judge.py` |

升级版本时须**同时**改：pin 常量、本文件、`wsl-prequalification.md`、镜像 digest，并在目标节点**重跑探针**归档 JSON。

#### 从源码编译二进制（Jenkins / 有网构建机）

构建机需 Go ≥ 1.21（v1.12.2 上游要求）、`git`、`make`（可选）。**构建阶段**可访问 GitHub；**运行节点**不得依赖外网。

```bash
export GOJUDGE_VERSION=v1.12.2
export GOJUDGE_COMMIT=955585f050286993ead2b2dd11c107d529b6b508

git clone --depth 1 https://github.com/criyle/go-judge.git
cd go-judge
git fetch --depth 1 origin "${GOJUDGE_COMMIT}"
git checkout "${GOJUDGE_COMMIT}"

# 产出 linux/amd64 静态链接二进制（与 WSL/K8s 节点一致）
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
  go build -trimpath \
    -tags seccomp,nomsgpack,grpcnotrace \
    -ldflags "-s -w" \
    -o go-judge \
    ./cmd/go-judge

# 记录 digest，写入探针报告或制品清单
sha256sum go-judge
./go-judge -version 2>/dev/null || true   # 运行时以 HTTP /version 为准
```

将 `go-judge` 放入镜像构建上下文（本仓库草案路径：`scripts/sandbox_probe/wsl_go_judge/go-judge`，**gitignore 不入库**）。CI 应把 `sha256` 写入构建日志或 `archiveArtifacts`，供探针 `--binary` 记录。

#### 随镜像发布的配置文件

与二进制同版本、同 commit 一并 COPY 进镜像（勿混用其他版本的默认 `mount.yaml`）：

| 文件 | 仓库路径 | 说明 |
| --- | --- | --- |
| `mount.yaml` | `scripts/sandbox_probe/wsl_go_judge/mount.yaml` | 容器内只读 bind `/bin` `/lib` `/usr` 等；**K8s 不在此 bind 宿主 pydata** |
| `seccomp.yaml` | `scripts/sandbox_probe/wsl_go_judge/seccomp.yaml` | 禁 `socket`/`connect` 等；**必须** `action: errno` |
| `Dockerfile` | `scripts/sandbox_probe/wsl_go_judge/Dockerfile` | base `harbor.deepfos.com/base/python3.11.9:debian12` + 镜像内 pip pydata |

启动参数（sidecar 容器内）：

```text
/opt/go-judge \
  -http-addr=0.0.0.0:5050 \
  -mount-conf=/opt/mount.yaml \
  -seccomp-conf=/opt/seccomp.yaml
```

就绪探针：HTTP `GET /version`，期望 `buildVersion` 与 pin 一致（如 `v1.12.2`）。

#### 镜像构建与推送（`dev/go-judge` 流水线）

```bash
# 构建上下文：scripts/sandbox_probe/wsl_go_judge/
# 须已放入上节编译的 go-judge 二进制
docker build -t harbor.deepfos.com/dev/go-judge:v1.12.2 \
  scripts/sandbox_probe/wsl_go_judge/
docker push harbor.deepfos.com/dev/go-judge:v1.12.2
# 生产引用 pin digest，禁 latest
```

镜像内 Python 已预装 `numpy<2` + `pandas`（见 Dockerfile `RUN pip install`），与 `subprocess-dev` 的 sandbox venv **相互独立**：sidecar 负责隔离执行，应用镜像仍须自己的 `.venv-sandbox`（§4）。

#### 构建后自检（构建机 / 联调节点）

```bash
# 1. 确认 seccomp 已生效：日志应有 loaded seccomp filter
docker run --rm --privileged \
  harbor.deepfos.com/dev/go-judge:v1.12.2 \
  2>&1 | head -20

# 2. 跑资格探针（sidecar 与 deep-agent 同 Pod 时 endpoint 用 127.0.0.1:5050）
python3 scripts/sandbox_probe/go_judge.py \
  --endpoint http://127.0.0.1:5050 \
  --report /tmp/go-judge-dev.json
# 期望 selection_eligible=true
```

#### 无外网节点的制品策略

| 阶段 | 允许 | 禁止 |
| --- | --- | --- |
| Jenkins 构建 | 拉 GitHub 源码、推 Harbor | — |
| K8s 运行节点 | 拉 Harbor pin 镜像 | 运行时 `git clone` / `curl` GitHub Release |
| 本仓库 | pin Dockerfile、mount、seccomp、版本号、可选 sha256 | 将 ~20MB 二进制长期提交 git（可用 LFS 或仅 Harbor 过渡） |

本地 WSL 预演细节（宿主直跑、bind 踩坑）见 [`wsl-prequalification.md`](wsl-prequalification.md)；**不替代**本节 K8s sidecar 制品流程。

### 3.3 探针与签字

探针示例（在 deep-agent 容器内；K8s sidecar 通常用镜像内 `python3`，无需 WSL 的 `PYTHONPATH` overlay）：

```bash
python3 scripts/sandbox_probe/go_judge.py \
  --endpoint http://127.0.0.1:5050 \
  --report /tmp/go-judge-dev.json
```

探针访问 `127.0.0.1:5050` 前须确保无 HTTP 代理劫持（WSL 常见 `127.0.0.1:6789` → 502）。`dev/deep-agent-sandbox` 流水线建议 `archiveArtifacts` 保存 JSON。

`selection_eligible=true` 后再议阶段 C；仍先只在 dev 切 Backend。

---

## 4. 应用镜像：sandbox venv（与 sidecar 正交）

凡启用 `subprocess-dev` 的环境（含 **test**），镜像内须有独立解释器，例如：

```dockerfile
COPY scripts/sandbox_venv/ /tmp/sandbox_venv/
RUN python3 -m venv /app/.venv-sandbox \
    && /app/.venv-sandbox/bin/pip install --no-cache-dir -r /tmp/sandbox_venv/requirements.txt \
    && rm -rf /tmp/sandbox_venv
ENV DEEP_AGENT_SANDBOX_PYTHON=/app/.venv-sandbox/bin/python
```

- 勿与应用 venv / DeepCube 宿主包合并；NumPy 保持 `<2`。
- Profile 变更须**重建镜像**；只改 ConfigMap 不够。
- `SANDBOX_ENABLED=false` 时可不校验 venv。

---

## 5. Deployment 要点（草案 · 探针后定稿）

主容器：保持现网非 privileged、8080 探针、现有资源量级。  
sidecar `go-judge`：pin digest；`-http-addr=0.0.0.0:5050`；就绪 `/version`；挂载节点 `/sys/fs/cgroup`（cgroup v2）；`privileged` 或改为探针确认的 `capAdd`。

**WSL 注意：** Docker sidecar 挂宿主 dbus 易失败（`Process 1 is a manager process, refusing`）；WSL 预演用**宿主直跑** + systemd。K8s 节点以 Pod 内 dbus/cgroup 为准，见 [`wsl-prequalification.md`](wsl-prequalification.md) §3.3。

阶段 B 可只部署 sidecar、**不改** Backend。阶段 C ConfigMap 示例：

```properties
DEEP_AGENT_SANDBOX_ENABLED=true
DEEP_AGENT_SANDBOX_BACKEND=go-judge
DEEP_AGENT_SANDBOX_GO_JUDGE_URL=http://127.0.0.1:5050
```

### test 资源建议（Pod 内存上限约 4Gi）

默认并发 4 × AS≈1.5Gi 虚拟地址偏紧，可收紧例如：

```properties
DEEP_AGENT_SANDBOX_BACKEND=subprocess-dev
DEEP_AGENT_SANDBOX_PYTHON=/app/.venv-sandbox/bin/python
DEEP_AGENT_SANDBOX_MAX_CONCURRENCY=2
DEEP_AGENT_SANDBOX_MEMORY_BYTES=402653184
DEEP_AGENT_SANDBOX_CPU_SECONDS=30
DEEP_AGENT_SANDBOX_MAX_PIDS=0
```

OOM 时优先降并发，勿先砍到过低 RSS 导致 Profile 起不来。

| 容器 | 请求 / 上限（初值） |
| --- | --- |
| deep-agent | CPU 10m–2；内存 1000Mi–4096Mi |
| go-judge | CPU 100m–2；内存 256Mi–2Gi（探针后调） |

单次执行产品限额（由 judge 参数 enforce）：内存 512Mi / pids 64 / 超时 30s。

---

## 6. 安全与回滚（审批口径）

| 问题 | 回答 |
| --- | --- |
| 为何 privileged？ | 每请求 cgroup/mount；能否降 cap 以 B 报告为准 |
| 主应用攻击面？ | 主容器保持非特权 |
| 网络 | 执行环境禁网（探针验证）；5050 不对外 |
| 安全 Backend 挂了？ | **禁止**自动降到 `subprocess-dev` 跑不可信代码 |
| 回滚 | ConfigMap 改回 `subprocess-dev` 或关闭 sandbox；可选移除 sidecar |

---

## 7. 发布顺序

1. **B / dev：** 双容器 + 探针 JSON 归档；test 不参与。
2. **C / dev 试点：** 切 `go-judge`，看 health `production_ready`；Console 联调。
3. **test 是否跟进：** 单独决策；未决前保持 `subprocess-dev`。

---

## 8. 协作接口

| 角色 | 责任 |
| --- | --- |
| 研发 | pin 版本、探针脚本、报告解读、阶段 C 环境变量与 health 契约 |
| 运维 | dev Deployment、两条流水线、Harbor `dev/*`、与 test 隔离 |
| 共同 | `selection_eligible=true` 前不在 test 启安全 Backend |

参考：`scripts/sandbox_probe/` · [`wsl-prequalification.md`](wsl-prequalification.md) · [go-judge](https://github.com/criyle/go-judge)
