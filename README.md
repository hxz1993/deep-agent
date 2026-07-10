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

**后端**（在 `deep_agent/` 下安装依赖并启动）：

```bash
cd deep_agent
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

后端启动后会挂载仓库根目录的 `frontend/`，访问 http://127.0.0.1:8000/ 即可使用聊天界面。

**前端独立预览**（可选）：

```bash
cd frontend
python -m http.server 5173
```

详细说明见 [`deep_agent/README.md`](deep_agent/README.md) 与 [`frontend/README.md`](frontend/README.md)。
