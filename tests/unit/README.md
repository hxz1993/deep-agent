# 单元测试指南

## 什么是单元测试（在本项目中）

满足以下全部条件时，测试放在 `unit/`：

- 不启动 MCP 子进程、不连真实 HTTP MCP
- 不经过 FastAPI `TestClient` 与 `AgentRunner`
- 只验证单个函数/类的输入输出，毫秒级完成

典型场景：配置解析、header 白名单、`AgentDeps` 构造、纯工具函数。

## 编写规范

1. **继承 `unittest.TestCase`**（与现有用例一致，不强制 pytest）。
2. **构造最小依赖**：例如用 Starlette `Request(scope=...)` 模拟 HTTP 请求，而不是拉起整个 app。
3. **不 patch 生产 `main.app`**：单元测试不应依赖应用 lifespan 或数据库。
4. **命名**：文件 `test_<模块>_<行为>.py`，方法 `test_<场景>_<预期>`。

## 示例：header 白名单

见 [`test_forward_headers.py`](test_forward_headers.py)：

- 直接调用 `extract_forward_headers(request, settings)`
- 断言不在白名单内的 header 被过滤

## 何时不应放在 unit/

以下情况请写到 [`../integration/`](../integration/README.md)：

| 想验证的内容 | 应使用的测试类型 |
|-------------|----------------|
| `POST /api/v1/chat` 是否把头传给 MCP | 集成 + `TestClient` |
| `build_mcp_toolset` + 真实 stdio server | 集成 |
| LLM 触发 tool call 的完整链路 | 集成 + `TestModel` |

单元测试通过 **不能** 证明 HTTP → Agent → MCP 端到端行为正确；曾出现过 unit/toolset 层通过但线上 header 为空的情况。

## 运行

```bash
PYTHONPATH=src python3.12 -m unittest discover -s tests/unit -p "test_*.py" -v
```
