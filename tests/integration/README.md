# 集成测试指南

本文总结 deep-agent **Agent + MCP + HTTP** 相关集成测试的实践，供后续功能开发参考。

## 什么是集成测试（在本项目中）

至少包含以下 **两层以上** 真实协作：

```
TestClient (HTTP)
  → chat.py / extract_forward_headers
  → AgentRunner → pydantic-ai Agent
  → MCPToolset（真实 stdio 子进程或 HTTP MCP）
  → MCP tool 执行
```

仅调用 `toolset.call_tool()` 并手动构造 `AgentDeps` 的测试，属于 **MCP 层集成**，仍 **不能** 替代 HTTP 端到端测试（见下文「教训」）。

## 核心原则

### 1. 用 TestClient + TestModel，不要调真 LLM

```python
from unittest.mock import patch
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

test_model = TestModel(
    call_tools=["echo_echo_request_headers"],  # 强制调用的 tool 名（含 MCP 前缀）
    custom_output_text="ok",
)
with patch("deep_agent.agent.factory._resolve_model", return_value=test_model):
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json={...}, headers={...})
```

- `call_tools`：指定要调用的 tool，名称与 registry 中一致（如 `text_tools_reverse`）。
- `custom_output_text`：tool 执行后的最终回复，避免多轮对话。

### 2. 不要用 `main.app` 直接做 TestClient

`deep_agent.main.app` 带有 **lifespan**：会连 PostgreSQL、从 DB 重载 MCP registry，**覆盖**测试里手动设置的 context。

正确做法：用 **最小 FastAPI 应用**，只挂载 `api_router`，并通过 `AppContext.for_testing()` 注入：

```python
from fastapi import FastAPI
from deep_agent.api.v1 import api_router
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.db.stores import InMemorySessionStore

app = FastAPI()
app.include_router(api_router)
init_context(
    AppContext.for_testing(
        settings=settings,
        registry=registry,          # 测试专用 registry
        store=InMemorySessionStore(),
    )
)
# 测试结束后调用 reset_context()
```

封装见 [`helpers/chat.py`](helpers/chat.py) 的 `create_test_app()` / `test_chat_client()`。

### 3. `/chat/stream` 需要 mock `get_db`

流式接口签名包含 `Depends(get_db)`，无数据库时会 503。测试中 override：

```python
from deep_agent.db.deps import get_db

async def override_get_db():
    yield MagicMock()

client.app.dependency_overrides[get_db] = override_get_db
```

并消费完 SSE 流（`list(response.iter_lines())`），否则 agent run 可能未完成。

### 4. 断言 MCP 侧行为：用 fixture server + 落盘

不要只在 Python 里 `patch inject_request_meta`（patch 时机易错，且与已构建的 toolset 脱钩）。

推荐模式（见 [`fixtures/header_echo_server.py`](fixtures/header_echo_server.py)）：

1. 专用 MCP server 提供 `echo_request_headers` tool。
2. 通过环境变量 `MCP_HEADER_RECORD` 把收到的 header 写入 JSON 文件。
3. HTTP 测试结束后读文件断言。

stdio 读 `ctx.request_context.meta.headers`；HTTP MCP 读连接上的 request headers。

### 5. 测试「无 header」的负例

`meta` 里只有 `progressToken`、没有 `headers`，通常表示 **`AgentDeps.request_headers` 为空**，`inject_request_meta` 未写入 `_meta`。应有一条集成测试覆盖：

- 请求 **不带** 白名单 header → MCP 记录为 `{}`。

这与线上「前端 EventSource 无法带头」的症状一致。

## 目录说明

| 路径 | 说明 |
|------|------|
| [`helpers/chat.py`](helpers/chat.py) | TestClient、TestModel、最小 app |
| [`helpers/mcp.py`](helpers/mcp.py) | 直接调 toolset、启动 HTTP echo 子进程 |
| [`fixtures/header_echo_server.py`](fixtures/header_echo_server.py) | 测试用 MCP server |
| [`test_chat_header_forwarding.py`](test_chat_header_forwarding.py) | **推荐模板**：完整 HTTP → MCP |
| [`test_mcp_header_forwarding.py`](test_mcp_header_forwarding.py) | MCP toolset 层（stdio / HTTP） |

## 教训：为什么 toolset 层测试会通过、线上却失败？

曾有的反模式：

```python
# 反模式：绕过 HTTP，手动塞 headers
deps = AgentDeps(session_id="t", request_headers={"Authorization": "Bearer x"})
await toolset.call_tool(..., ctx with deps)
```

这 **跳过了** `extract_forward_headers(request, settings)`。只要 `inject_request_meta` 正确，这类测试总会绿，但无法发现：

- 客户端未发送 header（如浏览器 `EventSource`）
- 白名单未包含业务 header
- `chat.py` 未把 header 写入 `AgentDeps`

**任何涉及「请求上下文进 MCP」的需求，必须有一条 `TestClient` 集成测试。**

## MCP header 透传（实现备忘）

| 传输 | 机制 |
|------|------|
| stdio（`mcps/*`） | `inject_request_meta` → `_meta.headers` |
| HTTP URL | `DynamicToolset` 每 run 合并 headers 建连 |

MCP server 内读取示例：

```python
meta = ctx.request_context.meta if ctx.request_context else None
headers = getattr(meta, "headers", None) or {}
```

## 前端 / SSE 注意

浏览器 **`EventSource` 不能设置自定义请求头**。若产品走 `/chat/stream` 且需要透传 auth，需额外方案（如 body 字段、query、或 cookie），不能假设 HTTP header 一定到达后端。

## 运行

```bash
.venv\Scripts\python.exe -m unittest discover -s tests/integration -p "test_*.py" -v
```

## 新增集成测试检查清单

- [ ] 使用 `create_test_app` 或等价物，避免生产 lifespan
- [ ] LLM 使用 `TestModel`，指定 `call_tools`
- [ ] HTTP 测试用 `TestClient` 的 `headers=` 传白名单内 header
- [ ] MCP 断言在 **server 侧**（落盘 / 返回值），不只断言 HTTP 200
- [ ] 至少一条负例（无 header / 错误 capability）
- [ ] 若测 stream，override `get_db` 并消费 SSE
