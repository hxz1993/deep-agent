# 无状态 Python Sandbox

DeepAgent 向模型暴露 `execute_python`：每次 Tool Call 在**全新**完整 CPython 进程里跑完即毁，无跨调用状态。v0.3.22 同一 Backend 契约增加受控 Skill 脚本请求，但只有生产就绪的安全 Backend 才向模型披露 `execute_skill_script`。联调默认 `subprocess-dev`；生产级硬隔离走阶段 C（阻塞于节点资格验证）。

| 文档 | 用途 |
| --- | --- |
| 本文 | 目标、选型、当前语义与进度 |
| [`implementation-plan.md`](implementation-plan.md) | 阶段任务与代码落点 |
| [`ops-kubernetes-sidecar.md`](ops-kubernetes-sidecar.md) | go-judge Sidecar / Jenkins / Rancher |
| [`wsl-prequalification.md`](wsl-prequalification.md) | WSL 宿主预演结果与踩坑（**非 K8s 签字**） |

---

## 1. 进度

| 阶段 | 状态 | 内容 |
| --- | --- | --- |
| **A** · `subprocess-dev` | **完成** | 表达式与Skill脚本内部协议、Worker、Toolset、health、独立 sandbox venv、`pydata`、rlimit、启用硬门槛、soft guardrails（seccomp 禁网 + Landlock 可写收束）；仍 `security_level=none`，脚本工具不向模型披露 |
| **B** · 安全 Backend 资格 | **探针代码已交付**；WSL 宿主预演 `selection_eligible=true`（见 [`wsl-prequalification.md`](wsl-prequalification.md)）；**目标 K8s 节点签字未完成** |
| **C** · 安全 Backend | **阻塞于 B** | go-judge（首选）或 NsJail Adapter |

本地快速验证：

```bash
python3.12 -m deep_agent.local_dev --config ~/.config/deep-agent/test.env --port 8000
curl -s http://127.0.0.1:8000/api/v1/health/sandbox
```

---

## 2. 产品目标与边界

**要做：** 模型显式调用的完整 CPython 数据计算；JSON `inputs` 进、stdout/stderr/异常/`result` 出；Profile 预装包（当前 `numpy<2`、`pandas`、`pydantic`）；无运行时 pip。安全 Backend 后续还可执行已授权Skill中的固定Python脚本，输入文件和正式结果路径由宿主控制。真实培训包中的 `validate_dynamic_blueprint.py` 只使用Python标准库，已在同一独立venv中跑通，不依赖未安装的 `openpyxl`或 `PyYAML`。

**不做：** 会话级变量 / Jupyter Kernel / Runtime TTL；Shell/Git/自定义 OCI；本课题内的 Artifact 出站与任意出网（Artifact 见 [`../08-artifact-export/`](../08-artifact-export/README.md)）。

| 项 | 决策 |
| --- | --- |
| 工具包 | v0.3.2 起为独立 `builtin:sandbox`，整包默认关闭 |
| 平台默认 | `SANDBOX_ENABLED=true`，`BACKEND=subprocess-dev` |
| 安全 Backend 失败 | 返回 `BACKEND_UNAVAILABLE`，**禁止**自动降到 `subprocess-dev` |

---

## 3. 关键选型（比过什么 → 为何当下）

### 3.1 联调路径：完整 CPython vs Pyodide 等

| 候选 | 结论 |
| --- | --- |
| **原地增强裸 CPython `subprocess-dev`** | **采用**：与阶段 C「同一解释器宇宙、只换隔离壳」同构，联调包（pandas）可沿用 |
| Coze / Deno+Pyodide、纯 Pyodide | **排除**：包宇宙分叉，阶段 C 几乎带不过去 |
| Dify Sandbox、E2B、Docker-in-Docker executor | **排除出主线**：治理/同构成本高，或不适配本仓库进程模型 |

### 3.2 生产隔离壳（阶段 B/C）

**第一版主线（进程级 jail，与 `subprocess-dev` 同构、可同 Pod sidecar）：**

| 候选 | 角色 |
| --- | --- |
| **go-judge** | **首选**资格验证与实施目标 |
| **NsJail** | go-judge 硬门槛失败时回退 |
| **isolate** | 前两者都失败再评估 |
| **crun / 自管 OCI** | 第一版不采用（治理成本高） |

选择闸门见 implementation-plan；运维 sidecar 见 ops 文档。**不得**用加权总分掩盖硬门槛失败。

**已评估、未进第一版主线（更强/更重，威胁模型升级后再开）：**

| 候选 | 结论 |
| --- | --- |
| **Firecracker** | microVM（KVM + 独立 guest kernel），隔离更强；需专用节点/嵌套虚拟化与 rootfs 编排，与现网「非特权主容器 + sidecar 探针」摩擦大 |
| **E2B** | 托管沙箱（底层常 Firecracker），产品省事；契约偏远端会话/API 与出站依赖，不是本机 `execute` 换壳，且与「流量不出 Pod / 禁网」叙事冲突（除非完全自建） |
| **gVisor** | 用户态内核拦 syscall，强于裸容器、轻于 microVM；需换 RuntimeClass，并对 `pydata`（numpy/pandas）做兼容与性能探针 |

再评估触发：多租户不可信代码、共享宿主机内核不可接受，或明确接受云托管/专用 Runtime 节点成本。

### 3.3 `subprocess-dev` 加固手段

| 手段 | 结论 |
| --- | --- |
| 独立 sandbox venv + Profile 硬门槛 | **采用**：与宿主 site-packages 隔离；启用 `subprocess-dev` 时缺 venv/包则拒绝启动 |
| POSIX rlimit（AS/CPU；NPROC 默认关） | **采用**：资源防呆；`RLIMIT_NPROC`≠cgroup `pids.max`，真 PID 帽留给 C |
| `preexec` seccomp 禁网 + Landlock 可写→`work_dir` | **采用**：比 Python 黑名单硬一档；失败**降级继续跑**；**不是**阶段 C |
| 极致 monkey-patch（urllib/open 全名单） | **不采用**：维护黑洞且可绕过 |
| 提前上 bwrap/nsjail 作联调 Backend | **不采用**：与阶段 C 重叠，联调阶段做重 |

---

## 4. 当前实现语义

```text
模型 → builtin:sandbox → execute_python(code, inputs)
     → PythonExecutionBackend
          ├─ SubprocessBackend     ← 现状（联调默认）
          └─ SecureLinuxBackend    ← 阶段 C（未实现）
     → ExecuteResult → 原始聊天历史
```

Skill脚本使用同一 Backend 的内部 `mode=script` 请求：宿主只复制已授权 `.py`、`inputs.json` 和
同Skill显式资源，固定从 `outputs/result.json` 读取正式结果。`execute_python` 的模型参数不变；
`subprocess-dev.production_ready=false`，因此只用于协议测试，不能让模型执行Skill脚本。

### 4.1 执行模型

- 每次 `execute`：**新** `python -m deep_agent.sandbox.worker`；临时 cwd；结束后删目录；超时/取消 `SIGKILL` 进程组。
- 无长驻 worker、无进程池复用状态；`state_scope=request`。
- `inputs` 注入全局名 `inputs`；多行 `exec`，**最后一行表达式** → `result`（`print` 只进 stdout）。
- DataFrame/Series → 定额预览（最多 10 行 `records`）。
- 并发：`Semaphore`，默认 4（`SANDBOX_MAX_CONCURRENCY`）。

### 4.2 `subprocess-dev` 能力与承诺

| 有 | 没有（仍 `security_level=none` / `production_ready=false`） |
| --- | --- |
| 独立 `.venv-sandbox` 解释器 | 每请求 cgroup / netns / 只读 rootfs |
| RSS 预算→`RLIMIT_AS`（×3）、CPU rlimit、输出截断 | 可宣称的硬隔离；隔离类错误码 |
| 尽力 seccomp 禁 inet + Landlock 可写收束 | 加载失败时的强制失败（降级继续） |
| 启用时 Profile/venv 硬门槛 | 自动升格为安全 Backend |

超限/拦阻映射为普通执行失败类码；**不得**伪造 `MEMORY_LIMIT` / `PID_LIMIT` / `SECURITY_POLICY_VIOLATION`（留给阶段 C）。

### 4.3 配置（常用）

```text
DEEP_AGENT_SANDBOX_ENABLED=true
DEEP_AGENT_SANDBOX_BACKEND=subprocess-dev
DEEP_AGENT_SANDBOX_PYTHON=.venv-sandbox/bin/python   # 镜像内常见 /app/.venv-sandbox/bin/python
DEEP_AGENT_SANDBOX_TIMEOUT=30
DEEP_AGENT_SANDBOX_MAX_CONCURRENCY=4
DEEP_AGENT_SANDBOX_MEMORY_BYTES=536870912            # RSS 产品预算；subprocess 再 ×3 → AS
DEEP_AGENT_SANDBOX_CPU_SECONDS=30
DEEP_AGENT_SANDBOX_MAX_PIDS=0                        # 0=不施加 RLIMIT_NPROC
```

venv 创建：`bash scripts/sandbox_venv/setup.sh`。Profile：`deep_agent/sandbox/profiles/pydata.json`（非热加载）。

### 4.4 错误码

| 码 | 谁产生 |
| --- | --- |
| `PYTHON_EXCEPTION` / `INVALID_REQUEST` / `TIMEOUT` / `CAPACITY_EXCEEDED` / `BACKEND_UNAVAILABLE` / `EXECUTION_FAILED` / … | `subprocess-dev` 与共用契约 |
| `MEMORY_LIMIT` / `PID_LIMIT` / `SECURITY_POLICY_VIOLATION` | **仅**安全 Backend |

### 4.5 代码落点

| 路径 | 职责 |
| --- | --- |
| `deep_agent/sandbox/types.py` · `backend.py` | 表达式/脚本请求、受控输入文件、结果与 health 契约 |
| `framing.py` · `worker.py` · `serialization.py` | 单 frame JSON · 一次性执行 · singledispatch 序列化 |
| `subprocess_dev/` | 子进程生命周期 · rlimit · soft seccomp/Landlock |
| `toolset.py` · `factory.py` · `profile.py` | Toolset 片段 · 工厂 · Profile |
| `api/v1/sandbox.py` | `GET /api/v1/health/sandbox` |

---

## 5. 联调与验收对话

### 5.1 前置

1. health：`enabled` / `smoke_ok` / `profile_ok` 为 true。
2. Agent 显式启用 **`builtin:sandbox`**；`capabilities: null` 不会加载该默认关闭包。
3. 建议系统指令要点：每次全新环境；表数据进 `inputs`；**最后一行用表达式**，勿用 `print` 收尾。

排障：`ImportError: pandas` → 查 sandbox venv / health `missing_packages`；`result=null` 且有 stdout → 多半是 `print` 结尾；`security_level=none` 表示当前为开发 Backend。

### 5.2 复制即用对话

**① 最简算术**

```text
请用 Python 计算 21 * 2，把代码传给 execute_python，最后告诉我结果。
```

预期：出现 `execute_python`；`code` 最后一行为表达式 `21 * 2`（非 `print`），`result` 为 `42`。若用了 `print(21 * 2)`，则 `stdout` 有输出、`result` 为 `null`——功能仍通，但未走结构化返回。

**② 带 inputs**

```text
用 execute_python 计算 inputs 里 values 的平均值。
inputs 用：{"values": [10, 20, 30, 40]}
code 里从 inputs 读取 values，最后一行用表达式返回平均值。
```

预期：工具参数含 `code` 与 `inputs`；`result` 为 `25.0`。

**③ 无状态（连续两轮）**

第一轮：

```text
用 execute_python 执行：x = 21
```

第二轮（同一会话）：

```text
再用 execute_python 计算 x * 2
```

预期：第二轮沙箱内没有 `x`，`error.code` 为 `PYTHON_EXCEPTION`（或模型说明变量不存在）。

**④ pandas 汇总**

```text
请用 execute_python 和 pandas 分析下面各区域销售额，按 region 汇总 amount 总和，
并告诉我哪个区域最高。代码最后一行必须是 groupby 表达式本身，不要用 print。

把下面数组放进 inputs.rows，在代码里 pd.DataFrame(inputs["rows"]) 再 groupby：

[
  {"region": "华东", "amount": 100},
  {"region": "华东", "amount": 200},
  {"region": "华北", "amount": 150},
  {"region": "华南", "amount": 180}
]
```

预期：`result.kind` 为 `dataframe` 或 `series`；华东合计 300 最高。下轮若再过滤，须把需要的行重新放进 `inputs`（无状态）。

**⑤ 触发 AS 上限（subprocess-dev 资源帽）**

默认 `SANDBOX_MEMORY_BYTES=512MiB` → 实际 `RLIMIT_AS`≈**1.5 GiB**。下面分配超过 AS，用于确认帽生效且**不会**出现隔离类错误码：

```text
请用 execute_python 执行下面代码（最后一行保持为表达式或可省略返回值）：
x = bytearray(2 * 1024 * 1024 * 1024)
len(x)
告诉我 tool_result 里的 status / error.code，不要自己猜内存是否够。
```

预期：

- `status` 为 `error`；
- `error.code` 为 `PYTHON_EXCEPTION`（如 `MemoryError`）或 `EXECUTION_FAILED`（进程被 AS 打死）；
- **不会**出现 `MEMORY_LIMIT` / `PID_LIMIT` / `SECURITY_POLICY_VIOLATION`；
- 父进程仍清理本次临时目录（见单元测试 `test_rlimit_kill_maps_to_execution_failed_not_memory_limit`）。

若默认 1.5 GiB AS 在机器上不易打满，可临时把 `DEEP_AGENT_SANDBOX_MEMORY_BYTES` 降到 `16777216`（16 MiB RSS → AS≈48 MiB），再让模型执行 `x = bytearray(80 * 1024 * 1024)`。

**⑥（可选）禁网 soft guardrail**

```text
用 execute_python 执行：
import socket
socket.socket(socket.AF_INET, socket.SOCK_STREAM)
```

预期：在 seccomp 生效的环境上应失败（`OSError` / `Operation not permitted` 等）；若环境降级未加载 seccomp，可能仍成功——不作为硬验收项。

---

## 6. 阶段 C 目标（未实现）

每请求：独立 cgroup v2、namespace、seccomp allowlist、只读 RootFS、禁网、非特权 UID。选型以阶段 B 节点报告为准，禁止在无证据时按本文猜实现。

---

## 7. 参考

- 探针：`scripts/sandbox_probe/`
- WSL 预演：[`wsl-prequalification.md`](wsl-prequalification.md)
- [go-judge](https://github.com/criyle/go-judge) · [NsJail](https://github.com/google/nsjail) · [isolate](https://github.com/ioi/isolate)
- Artifact：[`../08-artifact-export/README.md`](../08-artifact-export/README.md)
