# 无状态 Python 沙箱

DeepAgent 当前只有一套 Python 执行链路：`SubprocessBackend`。`execute_python` 与
`execute_skill_script` 都通过这套 Backend 在全新的 Python 子进程中执行；每次调用结束后进程和
临时工作目录都会销毁，不保留上一次调用的变量或文件。

## 两个模型工具

```text
execute_python(code, inputs)
→ 执行模型生成的完整 Python 代码
→ 最后一行表达式作为 result

execute_skill_script(skill_code, script_path, arguments, inputs, resource_paths)
→ 执行当前 Agent 已选择 Skill 中的固定 .py 脚本
→ 只复制显式授权的同 Skill 资源
→ outputs/result.json 作为正式 result
```

`execute_python` 在 Agent 启用 `builtin:sandbox` 时披露。`execute_skill_script` 还要求 Agent
选择了一个包含脚本的 Skill。Skill 脚本源码不能通过 `read_file` 读取。

## 当前执行边界

- 每次调用启动新的 `.venv-sandbox` Python 进程，工作目录使用系统临时目录。
- 预装包由 `deep_agent/sandbox/profiles/pydata.json` 定义，当前包含 numpy、pandas、pydantic；
  不支持运行时 `pip install`。
- 使用 Semaphore 限制并发，使用超时、CPU与地址空间限制和输出上限防止失控。
- 取消 Agent Run会终止本次子进程组，并在结束时删除临时目录。
- 只传递白名单环境变量；Linux环境尽力应用 seccomp禁网和Landlock写目录收束。
- `security_level=none`是当前健康接口对执行方式的事实描述，不影响两个工具是否可用。

## 配置

```text
DEEP_AGENT_SANDBOX_ENABLED=true
DEEP_AGENT_SANDBOX_BACKEND=subprocess-dev
DEEP_AGENT_SANDBOX_PYTHON=.venv-sandbox/bin/python
DEEP_AGENT_SANDBOX_TIMEOUT=30
DEEP_AGENT_SANDBOX_MAX_CONCURRENCY=4
DEEP_AGENT_SANDBOX_MEMORY_BYTES=536870912
DEEP_AGENT_SANDBOX_CPU_SECONDS=30
DEEP_AGENT_SANDBOX_MAX_PIDS=0
```

使用 `bash scripts/sandbox_venv/setup.sh` 创建独立解释器。运行时可通过
`GET /api/v1/health/sandbox` 查看 Profile、预装包、固定冒烟结果和资源上限。

## 代码入口

| 路径 | 职责 |
| --- | --- |
| `deep_agent/sandbox/types.py` | 表达式与脚本请求、受控输入文件、统一结果 |
| `deep_agent/sandbox/subprocess_dev/` | 子进程生命周期、资源限制和临时目录清理 |
| `deep_agent/sandbox/worker.py` | 一次性 Python执行与结果序列化 |
| `deep_agent/sandbox/toolset.py` | `execute_python` |
| `deep_agent/skills/script_tool.py` | `execute_skill_script` 的Skill权限和文件组装 |
| `deep_agent/api/v1/sandbox.py` | 沙箱健康接口 |

## 验收重点

- 两个工具使用同一个Backend和同一份Profile。
- `execute_python`最后一行表达式能形成非空结构化 `result`。
- Skill脚本只能选择当前Agent已授权的脚本和同Skill资源。
- 超时与取消会结束子进程，且不会残留工作目录。
- 第二次调用不能读取第一次调用的变量或文件。
