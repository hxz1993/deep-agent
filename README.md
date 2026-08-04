# DeepAgent

DeepAgent 是基于 FastAPI、Pydantic AI 和 SQLAlchemy 的多用户 Agent 服务，并
随服务发布一体化运维控制台。

当前产品版本为 `0.3.2`。`deep_agent.__version__` 是唯一代码版本来源，FastAPI、
`GET /info` 和 Console 顶部版本徽标均读取该值。后续正式发布默认递增
`MAJOR.MINOR.PATCH` 的第三位，只有明确规划重大版本时才调整前两位。产品版本不通过
环境变量或数据库覆盖，也不等同于 Alembic 数据库结构 revision。

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
