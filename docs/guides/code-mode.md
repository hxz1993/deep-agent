# PTC 程序编排使用指南

## 启用

在Agent能力中显式关联“程序编排（`builtin:ptc`）”，并分别关联需要使用的MCP包。
新Agent默认不关联；旧`builtin:code-mode`会在读取和导入时自动兼容。只有PTC、没有MCP时仍可启动，但只能使用code-only文件原语，通常
没有实际编排价值。

## 何时使用

- 一次调用、参数已知：直接调用普通 MCP。
- A 的结果直接给 B、需要并发只读查询、分页循环、过滤/排序/聚合或附件载荷中转：使用 `run_code`。
- pandas/numpy 计算或 Skill 脚本：使用 `builtin:sandbox` 的 `execute_python` / `execute_skill_script`。
- 读取给模型看的附件正文：使用外层系统工具`read_file`；`load_file_data`只在代码内原样中转。
- 下载、解析、转换或生成新文件：使用`execute_python`和`publish_path()`，再把返回的File ID交给PTC。

通用文件示例：

```python
payload = await load_file_data(file_id="file_001", encoding="base64")
uploaded = await upload_media(
    file_name=payload["name"],
    base64_content=payload["content"],
)
await send_message(media_id=uploaded["media_id"])
```

公开URL原样中转：

```python
payload = await load_file_data(url="https://example.com/report.png", encoding="base64")
uploaded = await upload_media(
    file_name=payload["name"],
    base64_content=payload["content"],
)
await send_message(media_id=uploaded["media_id"])
```

`file_id`和`url`必须且只能提供一个。URL只接受公开HTTP(S)地址，禁止凭据URL、回环、私网、链路
本地和云元数据目标，下载上限20 MiB；URL内容不会写入FileStore。

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
和 `asyncio.gather`，但没有宿主文件、环境变量、网络Socket、Shell、pip或第三方包。URL下载只
能经`load_file_data`这一受控code-only函数完成。工具参数和业务说明仍以外层普通MCP Tool Schema
为准，PTC动态目录只告诉模型可用函数名。
动态目录中的函数只能写在 `run_code` 代码中；只有同一协议名也出现在普通工具列表时，才允许
作为外层工具直接调用。可信稳定的返回Schema会生成结构化类型，其他MCP返回按通用JSON处理。

## 本地开发

固定使用稳定基线：

```bash
PYTHONPATH=src python3.12 -m deep_agent.local_dev \
  --config ~/.config/deep-agent/sqlite.env \
  --port 8000
```

数据库为 `data/deep-agent-local.db`，本地 FileStore 为 `data/deep-agent-local-files/`。不要为普通功能
测试新建空数据库，也不要把破坏性迁移测试指向该稳定库。

## 排障

- 看不到`run_code`：确认Agent已关联`builtin:ptc`；旧编码只用于兼容已有配置。
- 看不到某个 MCP：确认 Agent 已关联对应 MCP，且 ToolSearch 已披露该工具。
- 文件变量无效：Token 只能在生成它的同一 Run 使用，且必须作为完整字符串参数传递。
- 文件过大或文本超限：Base64固定上限20 MiB；文本改用外层`read_file`分页读取。
- 工具别名冲突：碰撞工具会保留原生调用，日志只记录冲突名称，不进入PTC。
