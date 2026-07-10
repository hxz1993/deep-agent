# deep-agent

通用 AI Agent 后端，基于 **FastAPI + [Pydantic AI](https://ai.pydantic.dev/)**。

- **Pydantic AI 负责**：agent loop、多 provider（改 model 字符串即可切换）、tool calling、结构化输出与校验重试、流式、MCP 客户端、usage 限额。
- **本项目负责**：可扩展的能力层（native tools / skill / MCP 统一为 toolset）、FastAPI 传输层（REST + SSE）、会话存储层（先内存，接口化）。

## 设计边界

```
HTTP (REST/SSE)  ->  AgentRunner  ->  Pydantic AI Agent  ->  Capabilities (toolsets)
                         |                                       |- native @tool
                     SessionStore                                |- skills/
                   (message_history)                             |- mcps/ (builtin MCP)
                                                                 |- config/mcp.json
```

`AgentRunner` 不依赖 FastAPI，是纯 async 入口，未来可作为 pydantic-graph 的一个节点接入编排。

## 快速开始

在仓库根目录下进入本目录后安装依赖并启动：

```bash
cd deep_agent

python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
# source venv/bin/activate

pip install -r requirements.txt

copy .env.example .env   # Windows (macOS/Linux: cp)
# 编辑 .env，设置 DEEP_AGENT_MODEL 和对应 provider 的 API key

uvicorn app.main:app --reload
```

打开 http://127.0.0.1:8000/docs 查看接口。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| GET | `/` | 基本信息 + 已注册能力 |
| GET | `/api/v1/tools` | 列出 native/skill/mcp 三类能力及工具 |
| POST | `/api/v1/chat` | 一次性对话，返回最终结果 |
| POST | `/api/v1/chat/stream` | SSE 流式（text/tool_call/tool_result/final 事件） |
| GET | `/api/v1/sessions` | 列出会话 |
| GET | `/api/v1/sessions/{id}` | 查看会话 |
| DELETE | `/api/v1/sessions/{id}` | 删除会话 |

请求示例：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "现在几点了？再算一下 (3+4)*2"}'
```

## 如何扩展能力

三种方式，都不需要改 runtime。

### 1. 加一个 native 工具
在 `app/capabilities/native/builtin.py` 的 toolset 上加一个带类型注解的函数（类型即 schema）：

```python
@toolset.tool_plain
def reverse(text: str) -> str:
    """Reverse a string."""
    return text[::-1]
```

### 2. 加一个 skill
新建 `skills/<name>/__init__.py`，导出模块级 `SKILL`：

```python
from pydantic_ai import FunctionToolset
from app.capabilities.registry import ToolInfo
from app.capabilities.skills.base import Skill

_toolset = FunctionToolset(instructions="该 skill 的使用说明。")

@_toolset.tool_plain
def my_tool(x: int) -> int:
    return x + 1

SKILL = Skill(name="my_skill", description="...", toolset=_toolset,
              tools=[ToolInfo("my_tool", "...")])
```

skill 的 `instructions` 会在该能力启用时自动注入到 agent 指令中。

### 3. 加一个 MCP server
两种方式，工具名都会自动加上 server 名前缀以避免冲突。

**外部 / 远程 server**：在 `config/mcp.json` 的 `mcpServers` 里加一条（Claude 风格配置）：

```json
{
  "mcpServers": {
    "fetch": { "command": "uvx", "args": ["mcp-server-fetch"] },
    "remote": { "url": "http://localhost:8000/mcp" }
  }
}
```

**仓库自带（builtin）server**：在根目录 `mcps/` 下新建 `mcps/<name>/server.py`，写一个 stdio MCP server，会被自动发现并注册为 `mcp:<name>`：

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-server")

@mcp.tool()
def greet(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}!"

if __name__ == "__main__":
    mcp.run()  # 默认 stdio
```

参考示例 `mcps/text_tools/`，更多说明见 `mcps/README.md`。

## 切换 provider / 模型

- 全局默认：改 `.env` 的 `DEEP_AGENT_MODEL`（如 `anthropic:claude-sonnet-4-5`）。
- 单次请求：在 `/chat` 请求体里传 `"model": "..."`。
- 限定能力：请求体里传 `"capabilities": ["native", "skill:calculator"]`。

## 生产化路线（后续）

- 上下文压缩（history processors）、HITL 审批（deferred tools / approval_required toolset）、输出护栏
- OpenTelemetry / Logfire 追踪 + token/cost 计量
- 持久化 `SessionStore`（Postgres/Redis）、provider 故障切换、多租户限流
- durable execution（Temporal/DBOS）、Pydantic Evals、长任务异步化、pydantic-graph 多 agent 编排
