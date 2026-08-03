# Python Sandbox 实施计划

设计基线：[`README.md`](README.md)。运维协作：[`ops-kubernetes-sidecar.md`](ops-kubernetes-sidecar.md)。

**架构不变式：** `execute_python` 只依赖 `PythonExecutionBackend`；换 Backend 不改 Tool / 请求 / 结果契约。安全 Backend 失败**不**回退到 `subprocess-dev`。

---

## 1. 进度

| 阶段 | 状态 | 备注 |
| --- | --- | --- |
| A · `subprocess-dev` | **完成** | 契约、Worker、Toolset、health、独立 venv、`pydata`、rlimit、硬门槛、soft guardrails |
| B · 资格验证 | **探针代码完成**；WSL 预演 **通过**；K8s 节点签字 **未完成** | `scripts/sandbox_probe/` · [`wsl-prequalification.md`](wsl-prequalification.md) |
| C · 安全 Backend | **阻塞于 B（K8s 签字）** | 凭 B 报告定 Adapter，禁止臆测实现 |

**下一步：** 在目标 K8s dev sidecar Pod 跑探针 → 归档 JSON → 选型 → 再开 C。

---

## 2. 执行规则（仍有效）

1. 先契约测试，再具体 Backend。
2. `subprocess-dev`：`security_level=none`，`production_ready=false`；可有 `resource_limits`；不得暗示 OS 级硬隔离。
3. 每请求新进程；无 Kernel / Runtime Registry / reset / destroy / TTL。
4. 模型不可覆盖可执行文件、env、mount、UID、seccomp、资源上限。
5. 安全候选须过目标 Linux 节点探针再产品化。
6. 启用 `subprocess-dev` 时 venv/Profile 失败 → **拒绝启动**；关闭 sandbox 不校验。
7. rlimit / soft guard / 输出超限 **不得**伪造 `MEMORY_LIMIT` / `PID_LIMIT` / `SECURITY_POLICY_VIOLATION`。
8. 不引入 Pyodide/Deno 作产品 Backend。

---

## 3. 代码落点（现状）

| 路径 | 职责 |
| --- | --- |
| `deep_agent/sandbox/types.py` · `backend.py` | 请求/结果/错误/health · Protocol |
| `framing.py` · `worker.py` · `serialization.py` | 单 frame · 一次性执行 · singledispatch 序列化 |
| `subprocess_dev/` | 子进程 + rlimit + soft seccomp/Landlock |
| `toolset.py` · `factory.py` · `profile.py` · `profiles/pydata.json` | Toolset 片段 · 工厂 · Profile |
| `options.py` · `core/context.py` · `capabilities/__init__.py` | 配置 · bootstrap 硬门槛 · 注册 |
| `api/v1/sandbox.py` | health |
| `scripts/sandbox_venv/` | 独立 venv 安装 |
| `scripts/sandbox_probe/` | B 资格探针（common / attacks / go_judge / nsjail） |
| `scripts/sandbox_probe/wsl_go_judge/` | WSL 宿主预演（自编 seccomp 二进制、mount overlay、`run_host_probe.sh`） |
| `tests/unit/test_sandbox_*.py` | 单元（含 `test_sandbox_subprocess_dev.py` · `test_sandbox_subprocess_guardrails.py`） |
| `tests/integration/test_chat_python_sandbox.py` | 聊天集成 |

---

## 4. 阶段 A（已完成 · 摘要）

联调默认 Backend：`subprocess-dev`（完整 CPython 子进程，无硬隔离承诺）。

- frozen 契约与限额；无 runtime 生命周期 API；单 frame JSON；Worker 每调用一进程。
- `SubprocessBackend`：白名单 env、临时目录、timeout/取消杀进程组、semaphore、health=`none`。
- v0.3.2 起 `execute_python` 并入默认启用的 `builtin:general`；Tool Call/Result 仍进入原始历史。
- 独立 sandbox 解释器（`SANDBOX_PYTHON`）；Profile `pydata`：`numpy>=1.26,<2` + `pandas`。
- 资源帽：RSS→`RLIMIT_AS`（×3）、CPU；`MAX_PIDS` 默认 0；输出截断；health 含 `resource_limits`。
- 启用时缺 venv/包 → bootstrap 失败；soft guardrails 仅本 Backend `preexec`，失败降级。

验收口径：独立 venv 可 import numpy/pandas；宿主多装包不可见；无隔离类错误码泄漏；相关 unittest + 聊天集成通过。

---

## 5. 阶段 B（进行中）

**产出：** 证据与选型，**不改** Tool 契约。

### 已完成

- 通用报告格式与攻击矩阵（`common.py` / `attacks.py`）
- go-judge / NsJail 探针脚本
- 运维 sidecar 协作文档
- **WSL2 宿主预演**（2026-08-02）：`selection_eligible=true`；细节见 [`wsl-prequalification.md`](wsl-prequalification.md)

### 未完成（依赖运维 / 节点）

| 项 | 说明 |
| --- | --- |
| WSL2 / 开发机探针 | **已做功能预习**（`run_host_probe.sh`），**不能**替代 K8s 生产签字 |
| **目标 K8s 节点探针** | 与最终 sidecar `securityContext` / cgroup 挂载一致 |
| 选型闸门裁决 | 见下 |
| 运维落地 | dev `deep-agent-sandbox` + 流水线；Harbor pin go-judge 镜像；归档 `selection_eligible` JSON |

### go-judge 实施要点（预演纠正）

| 项 | 要求 |
| --- | --- |
| 二进制 | 官方 Release **无 seccomp tag**；须自编或 Harbor 制品，pin **v1.12.2** + commit `955585f…` |
| seccomp | `socket`/`connect` 用 **`errno`**，勿 `kill_thread` |
| pydata | K8s 镜像内 pip（`wsl_go_judge/Dockerfile`）；WSL 宿主须 rsync 到 `/tmp` 再 bind，**不能**从 `$HOME` 直接 bind |
| per_request_cgroup | 客人 `0::/` 时，若存在 `gojudge.scope/containers`，探针做宿主 transient cgroup 校验 |
| 探针环境 | 清 HTTP 代理后再访问 `127.0.0.1:5050` |

### 攻击期望（摘要）

```text
读敏感路径 / 环境泄露     → denied
出网（loopback/公网等）   → denied（真隔离，非仅 ECONNREFUSED）
写 rootfs / mount / ptrace → denied
fork / 600MiB / 死循环 / 无限输出 → PID_LIMIT / MEMORY_LIMIT / TIMEOUT / OUTPUT_LIMIT
结束后 PID、cgroup、临时目录回到基线
```

### 选择规则

```text
go-judge 全部强制检查 PASS  → GoJudgeBackend
否则 NsJail 全部强制检查 PASS → NsJailBackend
否则 停止，单独评估 isolate
```

任一项硬失败（无每请求 cgroup、OOM 牵连他请求、seccomp 无法强制、必须暴露宿主关键路径、依赖不可用 Docker/systemd、Profile 跑不起来、清理有孤儿）→ **不选**该候选。

命令示例：

```bash
python3.12 scripts/sandbox_probe/go_judge.py \
  --endpoint http://127.0.0.1:5050 \
  --report /tmp/go-judge-dev.json

python3.12 scripts/sandbox_probe/nsjail.py \
  --nsjail /usr/local/bin/nsjail \
  --profile /opt/deep-agent-sandbox/nsjail.cfg \
  --report /tmp/nsjail-dev.json
```

---

## 6. 阶段 C（未开始）

B 报告定稿后再改本计划并实现，必须写清：

- Backend 文件与上游调用签名
- 固定 Profile / mount
- 构建部署、错误码映射、acceptance 命令
- 节点 capability / cgroup delegation、回滚方式

粗估量级：Adapter+Profile 数人日；安全验收数人日；K8s 发布数人日——以 B 证据重估。

---

## 7. 需求追踪

| 需求 | 覆盖 |
| --- | --- |
| 完整 CPython / pandas、无状态、JSON inputs | A |
| 固定依赖、无动态安装、Backend 可替换 | A、B/C |
| 不自动安全降级、不伪造隔离错误码 | A 规则 |
| 每请求 cgroup / OOM 隔离 / seccomp / 文件与网络隔离 | B→C |
| Tool 历史完整 | A 聊天集成 |

---

## 8. 节奏

```text
A        已完成（联调默认 subprocess-dev）
B        探针代码有；WSL 预演通过；待 K8s 节点签字与选型
C        阻塞于 B（K8s 签字）
```
