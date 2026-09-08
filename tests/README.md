# deep-agent 测试

测试按 **单元测试** 与 **集成测试** 分目录存放，在仓库根目录下使用 Python 3.12 运行：

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
| [`unit/`](unit/README.md) | 无外部进程、无网络的纯逻辑 | 白名单、配置解析等 |
| [`integration/`](integration/README.md) | HTTP API、Agent、真实 MCP 子进程 | header 透传、toolset 行为 |

新增测试时先判断属于哪一类，再放入对应目录；集成测试的公共 fixture 与 helper 放在 `integration/helpers/` 与 `integration/fixtures/`。

Console 测试：

```bash
node --test tests/frontend/console/*.test.mjs
```
