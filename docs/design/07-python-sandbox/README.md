# Python Sandbox 与会话运行环境

最近核对：2026-10-30。**当前 `test` 仍为无状态 Python 子进程；会话级 Sandbox 尚未合入 `test`，未完成产品验收。** 同事分支有候选实现，本轮0.15.0统一的是现有无状态执行，不合并会话沙盒、不升级依赖、不部署。

本轮产品方向：优先让活跃会话中的文件、工作目录、Python 与 Shell 操作连续可用；允许按成本设置有效期并到期清理，不要求工作区永久保留，也不把内存快照恢复作为首版前提。Agent 可申请使用、主动释放，系统负责超时回收。首次调用自动创建、后续复用是当前建议，尚未锁定实现。

2026-10-24 收口：以新版 Pydantic AI Workspace 规范为接入依据；Python／Skill／Shell 共用执行环境，PTC 按需串联。附件导入与成果收集由 Agent 服务统一负责，中间文件留工作区，明确交付的文件经输出目录收集后存 FileStore；下载中心登记独立处理。暂不新增平台 `save_file_data`。收集在单次执行结束还是整段 PTC 结束、旧文件接口兼容及具体后端仍待确定，详见设计第 7 节。

v0.14.7 已完成 PTC 对现有 Python／Skill、Web 和下载中心工具的双入口接入，沿用原执行后端；这不包含上述会话级工作区或 Shell。当前范围与限额见 [PTC 使用指南](../../guides/code-mode.md)。

- [会话级 Sandbox：产品与架构讨论](session-sandbox-design.md)：需求澄清、简单架构、生命周期及上游研究结论。
- [统一研发台账](../TODO.md)：`SANDBOX-001`。

## 已实现：无状态 Python 沙箱

DeepAgent 的完整 CPython 计算只有一套链路：`SubprocessBackend`。`execute_python` 与
`execute_skill_script` 都通过这套 Backend 在全新的 Python 子进程中执行；每次调用结束后进程和
临时工作目录都会销毁，不保留上一次调用的变量或文件。

## 两个模型工具

```text
execute_python(code, inputs, files)
→ 执行模型生成的完整 Python 代码
→ 最后一行表达式作为 result，可发布零到多个FileStore文件

execute_skill_script(skill_code, script_path, arguments, inputs, resource_paths, files)
→ 执行当前 Agent 已选择 Skill 中的固定 .py 脚本
→ 按原入口在skill/执行，自动携带同Skill Python模块和显式选中资源
→ outputs/result.json 作为正式 result，显式发布的产物上传并返回File ID
```

`execute_python` 在 Agent 关联 `builtin:sandbox` 时披露。`execute_skill_script` 还要求 Agent
选择了一个包含脚本的 Skill。Skill 脚本源码不能通过 `read_file` 读取。

v0.6.7使用显式`builtin:ptc`提供Harness`run_code`。它不运行完整CPython，
而是在Monty Python子集中编排当前已授权MCP；没有宿主文件、任意网络、Shell、pip和
第三方包。所以 `run_code` 不替代 `execute_python`，也不复用这一子进程 Backend。
详见 [`../20-code-mode/`](../20-code-mode/README.md)。

## 0.15.0 执行目录变化

0.15.0：普通 Python 仍执行传入代码；Skill 不再改名为临时 `main.py`，而是在一次调用的
`skill/` 副本按原入口、原包上下文执行。同目录与包内相对模块可引用，非代码资源仍须显式选择。
工作目录根的 `inputs.json`、`outputs/result.json` 保持；资源与 `.env` 按 `__file__` 定位。
Skill 与普通 Python 共用授权文件输入、显式产物上传及失败补偿，PTC汇总相同文件事实。

不做旧布局兼容，不改写旧历史。目录结构、限额及旧包适配见[Skill指南](../../guides/skills.md)。
这不是会话级文件系统或沙盒分支合并，不改变单次调用后清理的生命周期。

## 当前执行边界

- 每次调用启动新的 `.venv-sandbox` Python 进程，工作目录使用系统临时目录。
- 预装包由`src/deep_agent/sandbox/profiles/pydata.json`定义，包含numpy、pandas、pydantic，以及常用
  网络/网页、图片/图表、PDF与Office处理库；
  不支持运行时 `pip install`。
- 使用 Semaphore 限制并发，使用超时、CPU与地址空间限制和输出上限防止失控。
- 取消 Agent Run会终止本次子进程组，并在结束时删除临时目录。
- 只传递白名单环境变量，不继承平台凭据；Linux环境保留Landlock写目录收束。
- Python 不再按目标 IP 是否公网做代码级拦截，可请求部署网络可达的企业内网服务。
  普通 Python、Skill 脚本以及 PTC 调用这两个工具共用此规则；网络访问范围由部署的路由、防火墙或
  容器网络策略控制。本项不放宽 WebFetch、文件下载等独立工具的 SSRF 校验。
- `security_level=none`是当前健康接口对执行方式的事实描述，不影响两个工具是否可用。

## 文件输入与产物

```python
from deepagent_files import input_path, publish_path
from PIL import Image

source = input_path("平台文件ID")
target = publish_path("result.png", mime="image/png")
Image.open(source).save(target)
{"converted": True}
```

`input_path(file_id)`只解析当前会话已授权的附件、大工具结果或Python生成文件。`publish_path()`登记
本次执行产物；只有脚本成功结束后宿主才上传文件，未登记的临时文件随沙箱销毁。工具结果向模型
返回File ID、名称、MIME、大小和SHA-256，不返回宿主路径或Base64。后续Run可从原始Event恢复这些
File ID的会话授权，摘要本身不能授予权限。

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
| `src/deep_agent/sandbox/types.py` | 表达式与脚本请求、受控输入文件、统一结果 |
| `src/deep_agent/sandbox/subprocess_dev/` | 子进程生命周期、资源限制和临时目录清理 |
| `src/deep_agent/sandbox/worker.py` | 一次性 Python执行与结果序列化 |
| `src/deep_agent/sandbox/toolset.py` | `execute_python` |
| `src/deep_agent/skills/script_tool.py` | `execute_skill_script` 的Skill权限和文件组装 |
| `src/deep_agent/api/v1/sandbox.py` | 沙箱健康接口 |

## 验收重点

- 两个工具使用同一个Backend和同一份Profile。
- `execute_python`最后一行表达式能形成非空结构化 `result`。
- Skill脚本只能选择当前Agent已授权的脚本和同Skill资源。
- 超时与取消会结束子进程，且不会残留工作目录。
- 第二次调用不能读取第一次调用的变量或文件。
- 生成文件在沙箱销毁前上传，失败时不返回部分成功；历史和轨迹不包含Base64或宿主路径。
