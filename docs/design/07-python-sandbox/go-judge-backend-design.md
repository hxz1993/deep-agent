# DeepAgent go-judge 生产隔离 Backend 技术方案

> 状态：开发评审稿
> 更新时间：2026-08-12
> 当前基线：DeepAgent `0.3.23`、Alembic `004`
> 目标阶段：DeepAgent `0.3.24` / Python Sandbox Stage C；仍以目标K8s安全探针通过为前置条件

## 1. 结论

DeepAgent 目前已经具备统一的 `PythonExecutionBackend`、`execute_python`、Skill 脚本受控输入协议和结果协议，但当前实际 Backend `subprocess-dev` 只适合开发联调，不能安全执行不可信 Skill 脚本。

本方案增加 `GoJudgeBackend`，将 [go-judge](https://github.com/criyle/go-judge) 作为同 Pod Sidecar：

```text
模型
→ execute_python / execute_skill_script
→ DeepAgent PythonExecutionBackend
→ GoJudgeBackend
→ Pod 内 http://127.0.0.1:5050/run
→ go-judge 为本次请求创建受限进程、文件系统和 cgroup
→ 返回现有 ExecuteResult
```

最终效果：

- `execute_python` 与 `execute_skill_script` 共用同一套安全 Backend。
- 模型工具名、参数、返回结构、SSE、History和客户端协议不变。
- 不新增数据库字段或 Alembic版本。
- go-judge异常时绝不自动降级到 `subprocess-dev`。
- 只有目标 K8s环境的安全探针通过后，才允许 `GoJudgeBackend.production_ready=true` 并披露 `execute_skill_script`。

go-judge不是新的模型工具，也不是新的Agent框架。它只是把当前“Python代码实际在哪里运行”从 DeepAgent应用进程旁的普通子进程，换成受 Linux隔离约束的执行服务。

---

## 2. 为什么需要改

### 2.1 当前已经完成的能力

DeepAgent `0.3.22` 已经完成：

- `PythonExecutionBackend`统一接口。
- `mode=expression`：执行模型生成的Python，最后一行表达式作为结构化 `result`。
- `mode=script`：执行Agent已授权Skill中的固定 `.py`文件。
- Skill脚本只能携带显式 `inputs`、参数和同Skill内已授权资源。
- 结果固定从 `outputs/result.json`读取。
- 代码、输入、资源、stdout、stderr和结果大小限制。
- 超时、取消、History和上下文压缩配对协议。

这意味着业务协议已经存在，go-judge不需要重新设计模型工具。

### 2.2 当前缺口

现有 `subprocess-dev` 本质上仍是 DeepAgent Pod内的普通 CPython子进程：

- `security_level=none`。
- `production_ready=false`。
- rlimit、seccomp和Landlock只是开发阶段的尽力防护，不是完整隔离边界。
- 不能据此允许第三方Skill脚本在生产执行。

因此当前 `execute_skill_script`虽然协议和测试已经存在，但不会向模型披露。Stage C的目标不是增加更多工具，而是补齐生产执行壳。

---

## 3. 范围与不做项

### 3.1 本次要做

1. 实现 `GoJudgeBackend`，完整实现现有 `PythonExecutionBackend`。
2. `execute_python`和 `execute_skill_script`统一走 go-judge。
3. 复用现有表达式求值、结果序列化和Skill脚本协议，不另写一套业务语义。
4. 将DeepAgent限制转换为 go-judge的CPU、时钟、内存、PID和输出限制。
5. 将 go-judge状态确定性映射到现有 `SandboxErrorCode`。
6. 接通Pydantic AI正式取消：取消HTTP请求时同步终止受限进程。
7. 增加健康检查、目标K8s安全探针、日志和发布回滚流程。

### 3.2 本次不做

- 不提供Shell、Bash、Git、`pip install`或自定义命令。
- 不允许模型指定可执行文件、环境变量、UID、mount、seccomp或资源上限。
- 不允许任意出网。
- 不保存跨请求Python状态，不做Jupyter Kernel或长驻Runtime。
- 不导出任意脚本生成文件；第一版仍只读取固定结构化结果。
- 不增加Artifact下载协议。
- 不修改Agent、DeepCube或DeepTable前端协议。
- 不允许安全 Backend失败后静默使用 `subprocess-dev`。

---

## 4. 总体架构

```text
Kubernetes Pod

┌─────────────────────────────────────────────────────────────┐
│ deep-agent 容器                                             │
│                                                             │
│ Agent Run                                                   │
│   ├─ execute_python                                         │
│   └─ execute_skill_script（条件披露）                       │
│          ↓                                                  │
│ PythonExecutionBackend                                      │
│          ↓                                                  │
│ GoJudgeBackend                                              │
│   ├─ 请求校验                                               │
│   ├─ copyIn组装                                             │
│   ├─ HTTP生命周期/取消                                      │
│   └─ go-judge状态→ExecuteResult                             │
│          │ 127.0.0.1:5050                                  │
├──────────┼──────────────────────────────────────────────────┤
│ go-judge Sidecar                                            │
│          ↓                                                  │
│ 每请求隔离环境                                              │
│   ├─ 固定Python解释器                                       │
│   ├─ 只读系统文件                                           │
│   ├─ /w、/tmp为独立tmpfs                                   │
│   ├─ cgroup CPU/内存/PID                                   │
│   ├─ seccomp禁网和高风险syscall                            │
│   └─ 执行结束后销毁                                         │
└─────────────────────────────────────────────────────────────┘
```

职责划分：

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| DeepAgent | Agent授权、参数校验、文件白名单、结果协议、取消、日志 | Linux隔离实现 |
| `GoJudgeBackend` | DeepAgent协议与 go-judge协议转换 | 决定Agent能用什么Skill |
| go-judge | 进程、mount、cgroup、seccomp和资源执行 | 模型调用、History和业务权限 |
| K8s/运维 | Sidecar镜像、securityContext、cgroup挂载、制品和探针归档 | 修改模型工具协议 |

---

## 5. DeepAgent Backend设计

### 5.1 保持现有公共接口

不修改：

```python
class PythonExecutionBackend(Protocol):
    async def health(self) -> BackendHealth: ...
    async def execute(self, request: ExecuteRequest) -> ExecuteResult: ...
    async def aclose(self) -> None: ...
```

新增代码建议：

```text
deep_agent/sandbox/go_judge/
├── __init__.py
├── backend.py          # 并发、生命周期、health、ExecuteResult组装
├── client.py           # /version、/run HTTP协议
├── request.py          # copyIn、命令和资源限制映射
├── result.py           # go-judge状态和错误码映射
└── runtime_bundle.py   # 受信任Python运行内核打包
```

并修改：

- `deep_agent/sandbox/factory.py`：注册 `go-judge`。
- `deep_agent/options.py`：增加Sidecar URL和固定版本配置。
- `deep_agent/core/context.py`：启动时执行health和Profile验证。
- `deep_agent/api/v1/sandbox.py`：继续返回现有health结构。
- `tests/unit/test_sandbox_go_judge.py`：Backend单元测试。
- `tests/integration/test_chat_python_sandbox.py`：两种Backend共享契约测试。

### 5.2 不能另写一套Python执行语义

`subprocess-dev`当前已经定义：

- `inputs`注入方式。
- 最后一行表达式转 `result`。
- DataFrame/Series预览。
- stdout/stderr截断。
- Python异常结构。
- Skill脚本 `outputs/result.json`读取。

GoJudgeBackend必须复用这些实现。禁止在 `client.py`里临时拼一段新的Python脚本，形成第二套序列化规则。

推荐做法：

1. 将当前Worker依赖的受信任执行内核整理成固定 `runtime bundle`。
2. `subprocess-dev`继续使用同一源码。
3. GoJudgeBackend每次通过 `copyIn`放入这个小型固定bundle。
4. bundle来源于当前DeepAgent构建版本，不依赖Sidecar里残留的旧DeepAgent源码。
5. bundle通过固定入口执行，并固定写出 `outputs/execution-result.json`。

这样Sidecar只需要提供Python解释器和Profile三方包，DeepAgent升级结果协议时不会因为Sidecar中残留旧Worker而产生版本漂移。

### 5.3 请求目录

每次执行在 go-judge的独立 `/w`中构造：

```text
/w/
├── runtime/                   # DeepAgent受信任运行bundle
├── request.json               # request_id、mode、inputs、arguments
├── main.py                    # 模型代码或已授权Skill脚本
├── references/...             # 仅显式授权的同Skill资源
└── outputs/
    ├── result.json            # Skill脚本可选正式结果
    └── execution-result.json  # 受信任运行内核统一结果
```

`copyIn`规则：

- `main.py`只能来自 `ExecuteRequest.code`。
- `request.json`由DeepAgent生成，不接受模型提供原始JSON文件。
- `references/`只来自已经过 `SandboxInputFile`路径和大小校验的文件。
- 禁止绝对路径、`..`、软链接和覆盖 `runtime/`、`request.json`、`main.py`、`outputs/`。
- 不通过宿主路径bind用户文件；所有请求文件都使用 `copyIn`。
- 不使用 go-judge全局File Store保存用户请求内容。

`copyOut`只允许：

- `outputs/execution-result.json`。

第一版不返回任意文件列表，避免脚本借输出目录扩展数据出站面。

### 5.4 固定命令

命令由Backend生成，模型不可覆盖：

```text
python3 /w/runtime/runner.py /w/request.json
```

固定环境只包含运行必需值，例如：

```text
PATH=/usr/local/bin:/usr/bin:/bin
PYTHONNOUSERSITE=1
PYTHONDONTWRITEBYTECODE=1
PYTHONUNBUFFERED=1
```

不得透传DeepAgent容器环境变量，也不得透传数据库密码、Provider Key、用户Header或K8s Service Account Token。

### 5.5 资源限制映射

DeepAgent仍是产品限制的事实源，Backend负责单位换算：

| DeepAgent限制 | go-judge字段 | 第一版口径 |
| --- | --- | --- |
| `request.timeout_ms` | `clockLimit` | 请求上限，单位显式转换为ns |
| `SANDBOX_CPU_SECONDS` | `cpuLimit` | CPU时间上限 |
| `SANDBOX_MEMORY_MB` | `memoryLimit` | RSS/内存上限 |
| 固定安全值 | `procLimit` | 首版64；不沿用 `subprocess-dev` 的0值语义 |
| `MAX_STDOUT_BYTES` | stdout collector `max` | 64 KiB |
| `MAX_STDERR_BYTES` | stderr collector `max` | 64 KiB |
| `MAX_RESULT_BYTES` | copyOut/结果解析 | 512 KiB |

补充规则：

- DeepAgent本地 `Semaphore`继续限制并发，避免无限请求排队到Sidecar。
- go-judge `parallelism`不得低于DeepAgent允许的并发，或必须在压测后统一收紧。
- `request.timeout_ms`超过产品最大值时取产品最大值，不允许模型放大。
- HTTP客户端总超时应为执行 `clockLimit`加很短的协议收尾时间，不能使用无界等待。

### 5.6 结果和错误映射

优先读取受信任的 `outputs/execution-result.json`。该文件合法时，沿用当前Worker产生的 `status/result/error/truncated`，go-judge只补充资源状态和峰值。

如果受限进程被go-judge提前终止，没有合法结果文件，再按下表映射：

| go-judge结果 | DeepAgent结果 |
| --- | --- |
| `Accepted` + 合法结果文件 | 按结果文件返回 `ok`或 `PYTHON_EXCEPTION` |
| `Accepted` + 缺失/非法结果文件 | `BACKEND_PROTOCOL_ERROR` |
| `Time Limit Exceeded` | `TIMEOUT` |
| `Memory Limit Exceeded` | `MEMORY_LIMIT` |
| `Output Limit Exceeded` | `OUTPUT_LIMIT` |
| `Dangerous Syscall` | `SECURITY_POLICY_VIOLATION` |
| `Signalled`/`Non Zero Exit Status`且 `procPeak`达到上限 | `PID_LIMIT` |
| 其他 `Signalled`/`Non Zero Exit Status` | `EXECUTION_FAILED` |
| `File Error` | `BACKEND_PROTOCOL_ERROR` |
| `Internal Error`、容器创建失败 | `BACKEND_UNAVAILABLE` |
| 本地并发已满或Sidecar明确容量拒绝 | `CAPACITY_EXCEEDED` |
| HTTP连接失败、503、响应损坏 | `BACKEND_UNAVAILABLE`或 `BACKEND_PROTOCOL_ERROR` |

错误信息必须脱敏：

- 不返回宿主路径、mount配置、Sidecar内部堆栈或HTTP正文。
- Python自身异常可以继续返回受限traceback，但路径统一为沙箱内逻辑路径。
- `asyncio.CancelledError`绝不能转成 `ToolFailed`或 `BACKEND_UNAVAILABLE`，必须继续抛出以终止Agent Run。

### 5.7 正式取消

go-judge `v1.12.2` 的REST `/run`会把HTTP Request Context传入Worker；Worker继续把该Context传入执行环境，Context取消后会终止受限进程。因此DeepAgent可以使用现有Pydantic AI `CancellationToken`：

```text
用户POST /sessions/{id}/cancel
→ CancellationToken.cancel()
→ GoJudgeBackend.execute收到CancelledError
→ 主动取消并关闭当前 /run HTTP请求
→ go-judge Request Context取消
→ 受限进程退出并清理cgroup/tmpfs
→ Agent返回真实cancelled SSE
```

但是源码链路存在不等于目标环境一定正确。K8s验收必须实测：

- 在死循环、内存增长和Skill脚本执行中分别取消。
- 原SSE只出现一次 `cancelled`，不出现 `final`。
- Sidecar中对应进程、cgroup和临时文件在限定时间内消失。
- 取消后的下一次执行正常。

若实测“HTTP客户端取消但go-judge进程继续运行”，Stage C不得签字；不能只依赖30秒 `clockLimit`冒充正式取消。

### 5.8 健康状态与工具披露

启动检查：

1. `GET /version`可用，版本与固定pin一致。
2. 固定表达式 `1 + 1`成功。
3. Profile验证能导入 `numpy`、`pandas`、`pydantic`。
4. 固定禁网smoke被seccomp拒绝。
5. 目标K8s完整安全报告已经过人工签字。

健康返回：

```json
{
  "enabled": true,
  "backend": "go-judge",
  "state_scope": "request",
  "security_level": "linux-isolated",
  "production_ready": true
}
```

工具披露规则保持：

| 场景 | `execute_python` | `execute_skill_script` |
| --- | --- | --- |
| Agent未启用 `builtin:sandbox` | 不披露 | 不披露 |
| `subprocess-dev` | 可用于现有开发计算 | 不披露 |
| go-judge不健康或未通过生产门槛 | 不允许执行；明确不可用 | 不披露 |
| go-judge生产就绪，Agent无含脚本Skill | 披露 | 不披露 |
| go-judge生产就绪，Agent启用Sandbox并选择含脚本Skill | 披露 | 条件披露 |

Sidecar运行中暂时不可用时，调用返回 `BACKEND_UNAVAILABLE`；不得在本次请求里自动改走普通子进程。

---

## 6. Sidecar与K8s安全要求

### 6.1 制品固定

当前资格基线：

| 项 | 固定值 |
| --- | --- |
| go-judge tag | `v1.12.2` |
| commit | `955585f050286993ead2b2dd11c107d529b6b508` |
| license | MIT |
| build tags | `seccomp,nomsgpack,grpcnotrace` |

要求：

- 从固定commit自编Linux二进制。
- 记录并锁定二进制SHA-256；当前探针中的空digest必须补齐。
- Sidecar镜像使用Harbor digest部署，禁止 `latest`。
- 升级go-judge时，源码、二进制digest、镜像digest、探针和本文件一起更新并重新签字。

### 6.2 当前Sidecar草案必须先修正

仓库现有 WSL/Sidecar预演文件可以复用，但进入Stage C前必须修正：

1. **补齐 `pydantic>=2,<3`。** 当前Sidecar Dockerfile只有 `numpy`和 `pandas`，与 `pydata` Profile不一致。
2. **删除 `/.env` mount。** go-judge会自动加载容器根目录的 `/.env`；生产mount配置不得创建或挂载该文件。
3. **HTTP绑定改为 `127.0.0.1:5050`。** 同Pod容器共享网络命名空间，DeepAgent仍能访问；避免通过Pod IP暴露执行接口。
4. **填入真实二进制SHA-256。** 只有版本号不足以证明运行的是带seccomp的制品。
5. **删除与Python无关的通用编译器mount。** 第一版只执行固定Python，mount白名单应尽量小。
6. **禁止自动挂载Service Account Token。** Sidecar和受限环境均不需要访问Kubernetes API。

如果使用 `127.0.0.1`绑定，Kubernetes就绪检查使用容器内 `exec`访问 `/version`，不要使用默认访问Pod IP的 `httpGet`。

### 6.3 文件系统

建议最小mount：

- Python运行需要的 `/usr`、`/lib`、`/lib64`、动态链接配置，全部只读。
- `/w`和 `/tmp`为每个隔离环境的受限tmpfs。
- `/proc`按go-judge运行要求创建，并屏蔽敏感节点。
- 不挂载DeepAgent应用目录、用户Home、宿主 `/var/run`、Docker Socket和数据库配置。
- 不挂载平台Skill运行目录；Skill资源通过DeepAgent授权后按请求 `copyIn`。

### 6.4 网络和syscall

执行环境第一版默认完全禁网：

- seccomp拒绝 `socket/connect/bind/listen/send*/recv*`。
- 拒绝 `ptrace`、`process_vm_*`、mount、BPF、keyring、内核模块、reboot等。
- 对CPython启动可能触达的调用优先返回 `errno`，不得用错误策略把普通Python启动直接杀死。
- 网络探针必须验证是安全策略拒绝，而不是“目标地址刚好不可达”。

### 6.5 cgroup

目标K8s节点必须验证：

- cgroup v2委派和嵌套创建有效。
- 每个执行请求有独立cgroup。
- 内存超限只杀当前请求，不影响DeepAgent和其他执行。
- PID上限实际生效。
- 请求完成、失败和取消后cgroup回到基线。

如果Sidecar没有权限创建cgroup，go-judge仍可能启动，但相关限制不会生效；这种情况必须判定探针失败，不能只看 `/version=200`。

### 6.6 容器权限

- DeepAgent主容器继续非特权。
- go-judge Sidecar只获得探针证明必需的权限。
- 第一轮可以在隔离dev环境用 `privileged`完成资格测试；正式配置应根据探针尝试收敛为最小Capabilities。
- Sidecar不创建Kubernetes Service，不开放Ingress。

---

## 7. 配置

最小新增配置：

```properties
DEEP_AGENT_SANDBOX_ENABLED=true
DEEP_AGENT_SANDBOX_BACKEND=go-judge
DEEP_AGENT_SANDBOX_GO_JUDGE_URL=http://127.0.0.1:5050
DEEP_AGENT_SANDBOX_GO_JUDGE_VERSION=v1.12.2
```

继续复用：

```properties
DEEP_AGENT_SANDBOX_TIMEOUT=30
DEEP_AGENT_SANDBOX_MAX_CONCURRENCY=4
DEEP_AGENT_SANDBOX_MEMORY_MB=512
DEEP_AGENT_SANDBOX_CPU_SECONDS=30
```

说明：

- `SANDBOX_PYTHON`只属于 `subprocess-dev`，GoJudgeBackend不读取宿主解释器路径。
- 第一版 go-judge PID上限固定64，避免与 `subprocess-dev` 当前 `MAX_PIDS=0`语义混淆。
- URL只允许 `http://127.0.0.1`或显式允许的Pod内地址；禁止管理员把它配到公网第三方执行服务。
- 配置切回 `subprocess-dev`属于明确回滚操作，不是运行时自动fallback。

---

## 8. 测试方案

### 8.1 Backend单元测试

使用假的go-judge HTTP Server覆盖：

- `/version`成功、版本漂移、超时和非法响应。
- expression/script两种请求的 `copyIn`。
- 路径、参数、输入文件、大小限制不被绕过。
- CPU、clock、内存、PID和输出限制单位正确。
- 所有go-judge状态到 `SandboxErrorCode`的映射。
- 结果文件缺失、损坏、过大和request_id不一致。
- 连接失败、HTTP 4xx/5xx、容量不足和协议错误。
- `CancelledError`原样抛出，不转换成普通工具失败。
- `aclose()`后新请求返回 `BACKEND_UNAVAILABLE`。

### 8.2 共享契约测试

对 `SubprocessBackend`与 `GoJudgeBackend`运行同一批用例：

- 最后一行表达式。
- `inputs`。
- stdout/stderr。
- Python异常。
- numpy、pandas、pydantic。
- DataFrame/Series预览。
- Skill脚本参数和同Skill资源。
- `outputs/result.json`。
- 超限输入、资源和结果。

确定性结果必须一致；Backend字段、安全级别和真正隔离错误码允许不同。

### 8.3 目标K8s攻击探针

必须在最终镜像、节点、securityContext、cgroup和seccomp配置下执行：

| 攻击面 | 验收 |
| --- | --- |
| 读取敏感路径 | 被拒绝或路径不可见 |
| 环境变量泄漏 | 看不到DeepAgent/Provider/数据库/K8s Secret |
| loopback、集群和公网连接 | 被安全策略拒绝 |
| rootfs写入、mount、ptrace、BPF | 被拒绝 |
| fork压力 | 当前请求 `PID_LIMIT`，其他请求正常 |
| 内存增长 | 当前请求 `MEMORY_LIMIT`，Pod不OOM |
| 死循环 | `TIMEOUT` |
| 无限输出 | `OUTPUT_LIMIT`且响应有限 |
| 取消 | 进程、cgroup和tmpfs在限定时间内清理 |
| 并发 | 一个请求超限不影响其他请求 |

准入门槛：探针报告 `selection_eligible=true`，并归档镜像digest、二进制SHA-256、节点和Deployment配置。

### 8.4 DeepAgent集成测试

- 启用 `builtin:sandbox`的Agent正常调用 `execute_python`。
- 含脚本Skill在生产Backend下才披露 `execute_skill_script`。
- 无脚本Skill不披露脚本工具。
- 工具调用的SSE、History和上下文压缩配对不变。
- 生成中、排队中和执行中取消均收到真实 `cancelled`。
- Backend不可用时不产生 `final`伪成功，也不调用 `subprocess-dev`。
- 普通不使用Sandbox的聊天不创建go-judge执行请求。

### 8.5 真实业务包

继续使用 `excel-to-dynamic-report`中的 `validate_dynamic_blueprint.py`：

1. 读取同Skill Excel Asset。
2. 通过 `resource_paths`放入隔离目录。
3. 执行脚本。
4. 从固定结果文件读取结构化结果。
5. 取消一次执行并验证无残留。

---

## 9. 日志与监控

DeepAgent结构化日志只记录：

```json
{
  "event": "sandbox_execution_completed",
  "request_id": "...",
  "backend": "go-judge",
  "mode": "script",
  "status": "error",
  "error_code": "TIMEOUT",
  "duration_ms": 30017,
  "memory_peak_bytes": 123456789
}
```

不记录：

- Python代码。
- inputs内容。
- Skill资源内容和路径明细。
- stdout/stderr正文。
- Provider Key、Header、Cookie和平台Token。

最低监控项：

- 执行量和成功率。
- `TIMEOUT/MEMORY_LIMIT/PID_LIMIT/SECURITY_POLICY_VIOLATION`数量。
- `BACKEND_UNAVAILABLE/BACKEND_PROTOCOL_ERROR`数量。
- 执行耗时和峰值内存。
- 本地容量拒绝次数。
- Sidecar重启次数。

---

## 10. 实施顺序

### 阶段 0：修正制品草案

- Sidecar补 `pydantic`。
- 删除 `/.env` mount。
- HTTP改绑 `127.0.0.1`。
- 精简Python所需mount。
- 固定二进制和镜像digest。

### 阶段 1：目标K8s资格验证

- 建独立 `deep-agent-sandbox` dev Workload。
- 部署Sidecar但不切DeepAgent Backend。
- 跑完整攻击探针并归档。
- 未达到 `selection_eligible=true`则停止，不开发假安全适配。

### 阶段 2：GoJudgeBackend

- 抽取并复用受信任运行时bundle。
- 实现HTTP Client、请求映射、结果映射和Factory。
- 接入health、Profile和配置。
- 跑fake-server单元测试与共享契约测试。

### 阶段 3：dev联调

- 切 `SANDBOX_BACKEND=go-judge`。
- 验证 `execute_python`。
- 验证真实Skill脚本。
- 验证取消、容量、Sidecar故障和清理。

### 阶段 4：Test发布决策

- 代码、安全、运维三方确认报告。
- 先小流量部署。
- 观察错误率、资源和Sidecar重启。
- 确认后再允许生产Backend披露Skill脚本。

---

## 11. 回滚

### 11.1 Sidecar或Backend异常

优先回滚：

```properties
DEEP_AGENT_SANDBOX_ENABLED=false
```

结果：Sandbox工具不再可用，普通Agent继续服务。

如果产品仍需保留开发态 `execute_python`，可以由负责人明确改回：

```properties
DEEP_AGENT_SANDBOX_BACKEND=subprocess-dev
```

但必须保持：

- `security_level=none`。
- `production_ready=false`。
- `execute_skill_script`不披露。

任何代码不得在 go-judge执行失败后自动走这条回退。

### 11.2 版本回滚

- DeepAgent与Sidecar镜像均使用不可变digest。
- 回滚到上一对已验证的镜像组合。
- 不涉及数据库降级。
- 运行目录均为请求级临时数据，无迁移和清理脚本。

---

## 12. 开发验收清单

### 代码

- [ ] `GoJudgeBackend`完整实现 `PythonExecutionBackend`。
- [ ] 两种Backend复用同一表达式、序列化和脚本执行内核。
- [ ] 模型不能控制命令、env、mount、UID、seccomp和限制。
- [ ] HTTP错误、go-judge状态和DeepAgent错误码映射确定。
- [ ] 取消不会被吞掉或转换为普通工具错误。
- [ ] 绝无自动fallback。

### 制品与部署

- [ ] go-judge版本、commit、二进制SHA-256和镜像digest固定。
- [ ] 自编二进制确认启用seccomp。
- [ ] Sidecar Profile含 `numpy`、`pandas`、`pydantic`。
- [ ] 不存在 `/.env`、Secret和Service Account泄露路径。
- [ ] 5050只绑定Pod内loopback。
- [ ] 每请求cgroup、PID、内存和清理实测通过。

### 产品

- [ ] `execute_python`行为与现有版本一致。
- [ ] `execute_skill_script`只在正确条件下披露。
- [ ] SSE、History、Cancel和上下文压缩无需客户端改造。
- [ ] go-judge不可用时不伪成功、不降级执行。
- [ ] 普通聊天不触发go-judge。

---

## 13. 待开发与运维确认

以下问题必须在实施前确认，但不改变总体方案：

1. 目标dev/test节点的cgroup版本和Sidecar权限模型。
2. 自编 go-judge镜像的Harbor仓库、构建责任人和digest归档位置。
3. 安全探针报告由谁签字、保留多久。
4. DeepAgent和Sidecar是否按同一发布单绑定镜像组合。
5. Test首次开放 `execute_skill_script`的Agent白名单和观察窗口。

当前不需要产品决策：模型工具协议、Skill授权、数据库结构和前端展示均保持现状。

---

## 14. 参考

- [Python Sandbox设计总览](README.md)
- [现有实施计划](implementation-plan.md)
- [K8s Sidecar运维草案](ops-kubernetes-sidecar.md)
- [WSL资格预演](wsl-prequalification.md)
- [go-judge官方仓库](https://github.com/criyle/go-judge)
- 当前资格探针：`scripts/sandbox_probe/go_judge.py`
- 当前Backend接口：`deep_agent/sandbox/backend.py`
- 当前请求/结果协议：`deep_agent/sandbox/types.py`
