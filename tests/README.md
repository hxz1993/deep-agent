# deep-agent 测试

Python 测试按 **单元测试** 与 **集成测试** 分目录存放，共享辅助代码和样本在 `helpers/`、`fixtures/`；Console 测试使用 Node.js。以下命令均从仓库根运行：

```bash
# 全部
PYTHONPATH=src python3.12 -m unittest discover -s tests -p "test_*.py" -v

# 仅单元
PYTHONPATH=src python3.12 -m unittest discover -s tests/unit -p "test_*.py" -v

# 仅集成
PYTHONPATH=src python3.12 -m unittest discover -s tests/integration -p "test_*.py" -v

# 上下文压缩核心行/分支覆盖率门槛
python3.12 -m pip install -r requirements-dev.txt
PYTHONPATH=src python3.12 tests/check_context_compaction_coverage.py
```

| 目录 | 用途 | 文档 |
|------|------|------|
| [`unit/`](unit/README.md) | 单模块与隔离依赖回归 | 白名单、配置解析、Store 等 |
| [`integration/`](integration/README.md) | HTTP API、Agent、真实 MCP 子进程 | header 透传、toolset 行为 |
| [`helpers/`](helpers/) | 跨测试组共享辅助代码 | 身份、SQLite、模型替身、沙箱等 |
| [`fixtures/`](fixtures/) | 共用测试样本 | 会话 JSON、工具检索样本、测试专用 builtin MCP |
| [`integration/helpers/`](integration/helpers/) | 集成场景辅助代码 | HTTP/SSE、MCP 生命周期、会话准备 |
| [`integration/fixtures/`](integration/fixtures/) | 集成场景专用样本 | MCP server 与历史会话样本 |
| [`frontend/console/`](frontend/console/) | Console Node.js 测试 | 静态模块和交互契约 |

新增测试先按验证边界分类；跨测试组复用的 helper 与 fixture 放在根 `helpers/`、`fixtures/`，仅供集成场景使用的内容放在 `integration/` 对应子目录。测试专用 builtin MCP 固定放在 `fixtures/mcps/`，不得进入生产 `mcp_servers/` 扫描。

Console 测试：

```bash
node --test tests/frontend/console/*.test.mjs
```
