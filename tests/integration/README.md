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
from tests.integration.helpers.models import text_only_model, tool_call_model

# 纯文本回复（不调用任何 tool）
test_model = text_only_model("ok")

# 指定调用的 tool（名称与 registry 一致）
test_model = tool_call_model("builtin__confirm", text="done after confirm")
```

- `TestModel` 默认 `call_tools='all'`，会调用 Agent 实际挂载的**每一个** tool。内置 UI 工具也必须在 `capabilities` 中显式选择；`capabilities=null`、`[]` 或未包含相应 `ui:<name>` 时不会挂载。
- `text_only_model` / `tool_call_model`：见 [`helpers/models.py`](helpers/models.py)。
- `custom_output_text`：tool 执行后的最终回复文本。

### 2. 不要用 `main.app` 直接做 TestClient

`deep_agent.main.app` 带有 **lifespan**：会连 PostgreSQL、从 DB 重载 MCP registry，**覆盖**测试里手动设置的 context。

正确做法：用 **最小 FastAPI 应用**，只挂载 `api_router`，并通过 `AppContext.for_testing()` 注入：

```python
from fastapi import FastAPI
from deep_agent.api.v1 import api_router
from deep_agent.core.context import AppContext, init_context, reset_context
from tests.helpers.sqlite_stores import make_sqlite_stores_sync

app = FastAPI()
app.include_router(api_router)
stores, engine, session_factory = make_sqlite_stores_sync()
init_context(
    AppContext.for_testing(
        registry=registry,          # 测试专用 registry
        stores=stores,
        db_engine=engine,
        db_session_factory=session_factory,
    )
)
# 测试结束后调用 reset_context()
```

封装见 [`helpers/chat.py`](helpers/chat.py) 的 `create_test_app()` / `test_chat_client()`。

### 3. `/chat/stream` 用 `httpx.AsyncClient`，不要用 `TestClient.stream`

`TestClient.stream()` 在 SSE 场景下会**无限阻塞**（与 `is_disconnected` 探测有关）。集成测试应使用 `httpx.AsyncClient` + `ASGITransport`，并配合 `mock_request_disconnect()`：

```python
from httpx import ASGITransport, AsyncClient
from tests.integration.helpers.chat import mock_request_disconnect, read_chat_stream

# 或 helpers/chat.py 的 echo_chat_app + read_chat_stream
```

同步 `POST /chat` 仍可用 `TestClient`。

流式接口若未注入 sqlite `AppContext`，`Depends(get_db)` 会 503；使用 `make_sqlite_stores` / `testing_data_stores` 即可。

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
| [`helpers/session_seed.py`](helpers/session_seed.py) | 灌入会话 fixture、`wait_for_session_title` |
| [`helpers/export_session_fixture.py`](helpers/export_session_fixture.py) | 从 DB 导出会话 JSON fixture |
| [`helpers/mcp.py`](helpers/mcp.py) | 直接调 toolset、启动 HTTP echo 子进程 |
| [`fixtures/header_echo_server.py`](fixtures/header_echo_server.py) | 测试用 MCP server |
| [`test_chat_header_forwarding.py`](test_chat_header_forwarding.py) | **推荐模板**：完整 HTTP → MCP |
| [`test_chat_history_persistence.py`](test_chat_history_persistence.py) | **正常对话**：流式/同步完成后 history 含 assistant |
| [`test_chat_stream_cancel_and_history.py`](test_chat_stream_cancel_and_history.py) | 停止流、HITL 续聊、部分持久化 |
| [`test_mcp_header_forwarding.py`](test_mcp_header_forwarding.py) | MCP toolset 层（stdio / HTTP） |
| [`test_mcp_shared_registry_runtime.py`](test_mcp_shared_registry_runtime.py) | MCP shared registry 真实子进程集成：pid 存活检测、死 pid 自动重登、跨 registry 单例、lease manager 重试（Windows 跳过 filelock 竞争测试；生产用 Linux fcntl.flock） |

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
python3.12 -m unittest discover -s tests/integration -p "test_*.py" -v
```

## 新增集成测试检查清单

- [ ] 使用 `create_test_app` 或等价物，避免生产 lifespan
- [ ] LLM 使用 `text_only_model` / `tool_call_model`，勿裸用 `TestModel()`（默认 `call_tools='all'`）
- [ ] HTTP 测试用 `TestClient` 的 `headers=` 传白名单内 header
- [ ] MCP 断言在 **server 侧**（落盘 / 返回值），不只断言 HTTP 200
- [ ] 至少一条负例（无 header / 错误 capability）
- [ ] 若测 stream，override `get_db` 并消费 SSE
