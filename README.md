# deep-agent

通用 AI Agent 全栈项目（FastAPI 后端 + 静态前端）。

## 目录

| 路径 | 说明 |
| --- | --- |
| [`deep_agent/`](deep_agent/) | 后端：FastAPI、Pydantic AI、能力层、MCP、数据库迁移 |
| [`frontend/`](frontend/) | 前端：聊天 UI 与管理后台（静态 HTML/JS） |
| [`tests/`](tests/README.md) | 自动化测试：单元测试 + 集成测试（HTTP / Agent / MCP） |

## 运行测试

在仓库根目录（已安装 `deep_agent/requirements.txt` 依赖）：

```bash
venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

详见 [`tests/README.md`](tests/README.md)。

## 快速开始

**后端依赖**（在仓库根目录执行）：

```bash
python -m pip install -r requirements.txt
```

部署环境继续使用生产入口，身份和数据库配置由网关及部署环境提供：

```bash
uvicorn deep_agent.main:app --host 0.0.0.0 --port 8000
```

本地联调 Test 环境时，使用独立入口和仓库外的私有配置文件：

```bash
mkdir -p ~/.config/deep-agent
chmod 700 ~/.config/deep-agent
$EDITOR ~/.config/deep-agent/test.env
chmod 600 ~/.config/deep-agent/test.env

python -m deep_agent.local_dev \
  --config ~/.config/deep-agent/test.env \
  --port 8000
```

私有配置文件至少包含：

```dotenv
DEEP_AGENT_LOCAL_USER=<当前 Test 用户 ID>
DEEP_AGENT_LOCAL_TOKEN=<当前 Test Token>

# 其余运行配置按目标环境填写，例如 DEEP_AGENT_DBMS_*、EXPORT_*。
```

本地入口只监听 `127.0.0.1`，固定使用配置文件中的用户和鉴权；控制台仍可切换 `app / space / language`。配置文件必须位于仓库外，不能提交。线上入口不会读取这些本地身份变量。

后端启动后会挂载仓库根目录的 `frontend/`：

- 旧聊天页面：http://127.0.0.1:8000/
- 一体化控制台：http://127.0.0.1:8000/console/

**前端独立预览**（可选）：

```bash
cd frontend
python -m http.server 5173
```

详细说明见 [`deep_agent/README.md`](deep_agent/README.md) 与 [`frontend/README.md`](frontend/README.md)。
