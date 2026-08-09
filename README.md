# DeepAgent

DeepAgent 是基于 FastAPI、Pydantic AI 和 SQLAlchemy 的多用户 Agent 服务，并
随服务发布一体化运维控制台。

当前产品版本为 `0.3.14`。`deep_agent.__version__` 是唯一代码版本来源，FastAPI、
`GET /info` 和 Console 顶部版本徽标均读取该值。后续正式发布默认递增
`MAJOR.MINOR.PATCH` 的第三位，只有明确规划重大版本时才调整前两位。产品版本不通过
环境变量或数据库覆盖，也不等同于 Alembic 数据库结构 revision。

v0.3.4 在原有自动上下文压缩之外增加 session 级主动压缩 SSE 接口。Console 可在输入框旁
随时尝试压缩当前历史，并展示后端估算的上下文占用与当前有效 Checkpoint；原始聊天历史
仍完整保留。空、短或无明显压缩收益的历史由后端返回 `noop`，前端不替用户禁用入口。

v0.3.5 为普通和流式聊天增加结构化 `context_refs`。原始引用及 `metadata` 随会话历史保存，
模型只接收去掉 `metadata` 的 XML + JSON 派生投影；纯引用会话、分支、标题和上下文压缩
使用同一原始事实源，不新增数据库表或迁移。

v0.3.6 将上下文语义压缩收敛为单次摘要：在一个安全消息边界上选择待摘要前缀与最近尾部，
一次生成并复检 Checkpoint。429、502、503、504、网络重置或超时只允许使用完全相同的摘要
请求重试一次；无法安全装入模型窗口时不拆段归纳，也不覆盖已有 Checkpoint。

v0.3.7 统一自动与主动压缩的实时过程条、Checkpoint 保存结果和历史压缩标记；配置中心按
“Agent 设置 / 系统设置 / 辅助资源”重新分组，并增加不暴露凭据和本地完整路径的只读数据库
连接摘要。

v0.3.8 将 Console 工作台、配置中心、动态会话和工具过程中的字符及 emoji 图标统一为
随仓库发布的 Lucide 线性 SVG，并补齐动态渲染、主题尺寸和无障碍标签。

v0.3.9 将 Pydantic AI 升级到 2.26.0，DeepSeek V4 Flash/Pro 改由官方
`DeepSeekProvider` 与 Profile 处理，并删除本项目的模型消息兼容层。

v0.3.10 将停止生成收敛为正式取消链路：客户端调用 session 取消接口后继续读取原 SSE，
直到 Pydantic AI 返回真实 `RunCancelled`；部分回答、实际 usage 与 `state=interrupted` 历史
由后端持久化。用户停止不再由前端断流、定时器或本地伪造消息实现，断开连接只负责异常资源清理。

v0.3.11 针对 MySQL 短空闲超时增加连接池保护：连接使用前继续执行 pre-ping，且仅
MySQL 连接在 90 秒后主动换新；asyncmy/uvloop 在 pre-ping 阶段报告已关闭 Transport
时会被识别为断链并由 SQLAlchemy 重建连接，不重试已经开始执行的业务 SQL。

v0.3.12 为已授权 MCP 增加工具检索与渐进披露：候选超过 3 个时，首轮仅完整披露本地
词法检索命中的 Top 3，其余工具以稳定协议名目录提供，并可通过 Pydantic AI `search_tools`
按需加载 Top 5；小型 MCP 仍直接披露全部工具。无合法会话附件时附件 Toolset 不向模型
暴露。该优化不改变 ToolPackage 授权、MCP Header、真实执行和历史协议。

v0.3.13 完成 Pydantic AI 2.26 上下文压缩兼容收口：同一个官方 `CancellationToken`
贯穿正式回答与内部摘要，停止生成时不会因摘要失败回退后继续回答；Local 与 Provider Native
工具统一按官方 Call/Return 基类配对，避免原生工具历史阻断主动压缩或产生错误修补。现有
单次摘要、Checkpoint、主动压缩接口和 SSE 协议保持不变。

v0.3.14 修复压缩与工具检索联调边界：每个 Pod 固定一个异步 Gunicorn Worker，使进程内
活动 Run 与取消请求一致命中；主动压缩先返回终态并关闭 SSE，客户端再刷新历史和上下文状态。
MCP 预选以用户问题为主、Context Reference 标签仅作无命中回退，同时识别否定式只读表达，
过滤明显副作用工具，并在单次 Run 内排除已披露工具和重复搜索。Pydantic AI 的跨 Run 工具
发现历史恢复语义保持不变。

## 项目结构

| 路径 | 职责 |
| --- | --- |
| [`deep_agent/`](deep_agent/AGENTS.md) | API、Agent 运行时、能力装配、会话和数据库 |
| [`frontend/`](frontend/AGENTS.md) | `/console/` 静态控制台 |
| [`mcps/`](mcps/AGENTS.md) | 仓库内置 MCP Server |
| [`skills/`](skills/) | Python Skill |
| [`deepfos_platform/`](deepfos_platform/README.md) | 平台配置、客户端、服务发现和部署运行时支撑 |
| [`maintenance/`](maintenance/README.md) | 人工维护程序，不属于 Agent 工具 |
| [`tests/`](tests/README.md) | Python、接口、MCP 和 Console 测试 |
| [`docs/`](docs/README.md) | 当前说明、技术设计和交互原型 |

完整边界见
[`docs/guides/project-structure.md`](docs/guides/project-structure.md)。

## 本地启动

默认使用本机 Python 3.12。安装依赖：

```bash
python3.12 -m pip install -r requirements.txt
```

若启用 Python Sandbox（默认 `DEEP_AGENT_SANDBOX_ENABLED=true`、backend=`subprocess-dev`），
还需**独立** sandbox 解释器（与宿主 `venv` 分离，预装 numpy/pandas）。  
`local_dev` **默认**会在缺解释器时自动执行 setup（可用 `--no-sandbox-env` 关闭）：

```bash
# 手动创建（可选；local_dev 默认也会补齐）
bash scripts/sandbox_venv/setup.sh
```

说明见 [`scripts/sandbox_venv/README.md`](scripts/sandbox_venv/README.md)。

连接 Test 数据库进行本地联调时，使用仓库外的私有 Profile：

```bash
python3.12 -m deep_agent.local_dev \
  --config ~/.config/deep-agent/test.env \
  --port 8000
```

Profile 至少包含数据库配置和本地可信身份，权限应设为 `0600`。不要把 Token、
Cookie、数据库密码或 API Key 提交到仓库。Sandbox 相关项可按需写入同一 Profile，例如
`DEEP_AGENT_SANDBOX_PYTHON`（默认解析为仓库根 `.venv-sandbox/bin/python`）。
test 固定镜像部署可设 `DEEP_AGENT_SANDBOX_ENSURE_VENV=true`，由 gunicorn 主进程在 fork 前创建临时 venv（如 `/tmp/.venv-sandbox/bin/python`），无需为 pydata 重建镜像。

若不想自动建 venv（例如离线环境），加 `--no-sandbox-env`；此时缺解释器会直接退出并提示手动 setup。

访问入口：

- Console：<http://127.0.0.1:8000/console/>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>
- Sandbox：<http://127.0.0.1:8000/api/v1/health/sandbox>

部署环境使用：

```bash
uvicorn deep_agent.main:app --host 0.0.0.0 --port 8000
```

`deep_agent.local_dev` 只用于本机回环调试，不用于容器部署。容器镜像若启用
`subprocess-dev`，构建阶段同样需要执行 `scripts/sandbox_venv/setup.sh`（或等价步骤），
并设置 `DEEP_AGENT_SANDBOX_PYTHON`；详见
[`docs/design/07-python-sandbox/ops-kubernetes-sidecar.md`](docs/design/07-python-sandbox/ops-kubernetes-sidecar.md) §5.3。

## 数据库基线

v0.3.0 将发布前的 Alembic `001`～`023` 历史压缩为唯一基线 `001`。新数据库直接
执行 `alembic upgrade head`；已有旧库不能重放迁移，必须先使用
`python3.12 -m maintenance.rebaseline_database` 校验最终结构，再通过 Alembic
`stamp --purge 001` 重设版本。完整备份和切换命令见
[`maintenance/README.md`](maintenance/README.md)。

## 工具包类型

DeepAgent 当前支持：

- 内置工具：
  - `builtin:general`（通用工具）：默认启用，只包含 `get_current_time`。
  - `builtin:sandbox`（Python 沙箱）：默认关闭，只包含 `execute_python`。
  - `builtin:attachment`（附件读取）：默认关闭，包含 TXT、DOCX、XLSX 按需读取工具。
- `skill:<name>`：Python Skill，组合 Instructions 和函数工具。
- `mcp:<name>`：URL、stdio 或 builtin MCP。
- `ui:builtin`：代码内置的确认和候选选择交互，默认关闭。
- `ui:<package>`：数据库管理的手工 UI 工具包，Agent 按包启用。

产品配置单位是 Tool Package，每个包严格包装一个 Pydantic AI Toolset。Agent 的
`capabilities` 字段保存工具包编码；当前不提供包内逐工具授权。Output Tool 是最终输出契约，
不属于工具包，也不进入 `capabilities`。

仓库不再注册 Cursor 专属 Skill。未来需要长 Skill 渐进披露时，在通用
`skill:` 机制内设计。

## 测试

```bash
python3.12 -m unittest discover -s tests -p 'test_*.py'
node --test tests/frontend/console/*.test.mjs
```

具体开发和测试要求见根目录及子目录的 `AGENTS.md`。

设计课题和后续待办见 [`docs/design/README.md`](docs/design/README.md) 与
[`docs/design/TODO.md`](docs/design/TODO.md)。
