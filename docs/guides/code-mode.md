# Code Mode 使用指南

## 启用

在 Agent 的能力中显式关联“代码编排（`builtin:code-mode`）”，并分别关联需要使用的 MCP 包。
新 Agent 默认不关联；只有 Code Mode、没有 MCP 时仍可启动，但只能使用 code-only 文件原语，通常
没有实际编排价值。

## 何时使用

- 一次调用、参数已知：直接调用普通 MCP。
- A 的结果直接给 B、需要并发只读查询、分页循环、过滤/排序/聚合或附件载荷中转：使用 `run_code`。
- pandas/numpy 计算或 Skill 脚本：使用 `builtin:sandbox` 的 `execute_python` / `execute_skill_script`。
- 读取给模型看的附件正文：使用外层系统工具 `read_file`；`load_file_data` 只在代码内中转。

通用文件示例：

```python
payload = await load_file_data(file_id="file_001", encoding="base64")
uploaded = await upload_media(
    file_name=payload["name"],
    base64_content=payload["content"],
)
await send_message(media_id=uploaded["media_id"])
```

并发只读示例：

```python
import asyncio

items = await asyncio.gather(query_a(), query_b(), query_c())
sorted({row["id"]: row for group in items for row in group}.values(), key=lambda x: x["id"])
```

不要打印或返回 `payload["content"]`，不要让大中间结果重新进入模型上下文，不要为一次简单工具调用
编写代码，也不要在不确定写入失败后重放同一副作用操作。

## 运行边界

`run_code(code: str, restart: bool=false)` 使用 Monty Python 子集。支持 `await`、条件、循环、容器处理
和 `asyncio.gather`，但没有宿主文件、环境变量、网络 Socket、Shell、pip 或第三方包。工具参数和
业务说明仍以外层普通 MCP Tool Schema 为准，Code Mode 动态目录只告诉模型可用函数名。

## 本地开发

固定使用稳定基线：

```bash
python3.12 -m deep_agent.local_dev \
  --config ~/.config/deep-agent/sqlite.env \
  --port 8000
```

数据库为 `data/deep-agent-local.db`，本地 FileStore 为 `data/deep-agent-local-files/`。不要为普通功能
测试新建空数据库，也不要把破坏性迁移测试指向该稳定库。

## 排障

- 看不到 `run_code`：确认 Agent 已关联 `builtin:code-mode`。
- 看不到某个 MCP：确认 Agent 已关联对应 MCP，且 ToolSearch 已披露该工具。
- 文件变量无效：Token 只能在生成它的同一 Run 使用，且必须作为完整字符串参数传递。
- 文件过大或文本超限：Base64 固定上限 20 MiB；文本改用外层 `read_file` 分页读取。
- 工具别名冲突：碰撞工具会保留原生调用，日志只记录冲突名称，不进入 Code Mode。
