# deep-agent 测试

Python 测试继续使用 `unittest`，Console 使用 Node.js 原生测试，不需要额外安装测试框架。以下命令均从仓库根运行，优先使用已有 `.venv`。

## 日常与发布入口

```bash
# 默认完整回归：全部 Python + Console；发布前使用
.venv/bin/python scripts/run_tests.py

# 显式纯逻辑快速组 + Console 全量，不代表完整验收
.venv/bin/python scripts/run_tests.py --suite quick

# 相对基线的提交、暂存、未暂存及非忽略新文件一起选测
.venv/bin/python scripts/run_tests.py --suite affected --base HEAD

# 先看将执行的文件和原因，不导入或执行测试
.venv/bin/python scripts/run_tests.py --suite affected --base HEAD --list

# 可选安全摘要；默认不写报告文件
.venv/bin/python scripts/run_tests.py --json-report 'output/TMP to delete-tests/full.json'
```

默认显示最慢 20 项，包含初始化和清理；`--durations 0` 可关闭。报告分别记录收集／执行耗时、用例状态、失败、错误、跳过、子测试及选择原因，不保存异常或日志正文。失败返回非零，跳过不计作已验证通过。

`affected` 使用 `tests/helpers/test_groups.json` 的显式领域清单；多个领域取并集。修改测试文件会运行自身及其使用者，修改 Python helper 会沿本地 import 反向追踪直接和间接使用者。Console 代码或测试修改仍跑 Console 全量。共享装配、数据库、依赖、测试入口、未知路径、删除／无法解析的辅助模块，以及失效的领域清单均回退完整回归；不得把影响面不明当成无测试。

快速组清单位于 `tests/helpers/test_selection.py`，逐项登记纯逻辑测试，不含真实数据库、HTTP 或子进程执行。`unit/` 目前仍包含部分边界测试，不能把整个目录称为快速组。本轮不大搬目录、不并行执行，也不让测试共用可变数据库状态。

## 直接运行与目录

保留原有入口；统一 `-t .`，使模块以 `tests.*` 身份导入：

```bash
# 全部
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -t . -p "test_*.py" -v

# 仅单元
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests/unit -t . -p "test_*.py" -v

# 仅集成
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests/integration -t . -p "test_*.py" -v

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

共用初始化使用不继承 `TestCase`、不带 `test_*` 方法的 fixture／mixin。不要为借用 `setUp` 而把另一个测试类导入当前模块，否则 `unittest` 会重复收集；确需在不同初始化下继承整组行为时，用模块引用基类并保留场景。运行入口遇到重复测试 ID 会报错，不静默去重。参数场景可以用具名 `subTest`，不同权限、取消、事务和失败补偿边界仍独立验证。

Console 测试：

```bash
node --test tests/frontend/console/*.test.mjs
```
