# PTC 程序编排使用指南

> 2026-10-24：以下工具扩展与资源限制为v0.14.7实现行为；测试结果另列。

## 启用

在Agent能力中显式关联“程序编排（`builtin:ptc`）”，并分别关联需要使用的MCP、Web、Python沙箱及Skill。
新Agent默认不关联；旧`builtin:code-mode`在读取和导入时兼容。关联PTC不会自动授予其他工具权限。

当前实现：已授权且已披露MCP，以及当前已装配的`execute_python`、
`execute_skill_script`、`web_search`、`web_fetch`、`export_file_to_download_center`同时提供原生与PTC入口。
`load_file_data`仍为代码内原语；`read_file`、`parse_document`、`web_image_view`、UI/Deferred、
发现、计划、历史和Resource保持外层。没有MCP时，可编排其他已装配白名单工具；仅选PTC不会生成这些工具。
不增加`save_file_data`或共享工作区。

同一工具的两个入口使用相同参数。例如，小数据直接原生调用`execute_python`，提交：

```json
{"code":"{'total': sum(inputs['values'])}","inputs":{"values":[1,2,3]}}
```

结果需继续交给另一个工具时，可在`run_code.code`中编排；以下`query_values`仅代表已披露、返回数值数组的MCP函数，须替换为实际名称与参数：

```python
values = await query_values()
computed = await execute_python(
    code="{'total': sum(inputs['values'])}",
    inputs={"values": values},
)
if computed["status"] != "ok" or any(computed["truncated"].values()):
    raise ValueError("Calculation failed or was truncated")
computed["result"]
```

大数据在第二种形式中直接通过变量传递。原生大结果若已外置，使用Python的`files`读取授权文件，
不要让模型把明细重新抄进`inputs`。`truncated`是逐项布尔标记字典，不能把字典本身作为是否截断的判断。

## 何时使用

- 一次调用、参数已知：直接调用普通 MCP。
- A 的结果直接给 B、需要并发只读查询、分页循环、过滤/排序/聚合或附件载荷中转：使用 `run_code`。
- pandas/numpy计算或固定Skill脚本：使用已关联Sandbox的`execute_python` / `execute_skill_script`；可原生调用，也可在PTC里直接传代码字符串和JSON inputs。Skill仍只接受原有参数与已索引脚本，不新增文件输入或发布参数。
- 读取给模型看的附件正文：使用外层系统工具`read_file`；`load_file_data`只在代码内原样中转。
- 解析、转换或生成文件：使用`execute_python(files=...)`读取授权文件，`publish_path()`登记产物，返回的File ID可交给下一次Python或下载中心导出；无需让模型转抄文件内容。

Skill返回完整HTML时，也可在同一段PTC内原样传给下载中心的`source={"type":"content","content":html}`；
已有文件则沿用`platform_file`来源。发布仍须显式调用，不自动保存或发布每个中间变量。

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

示例中的查询、上传和发送名称是占位别名，实际名称和参数以当前动态目录与原生Schema为准。
不要打印或返回`payload["content"]`，不要让大中间结果重新进入模型上下文，不要为简单调用编写代码，
也不要在不确定写入失败后重放同一副作用操作。

## 数据交接与限额

当前实现：PTC内部开放函数拿到完整返回，绕过面向模型的Token外置、截断和
分页包装；普通调用和外层`run_code`最终返回仍受原有保护。原工具自身的分页、预览、安全下载和
输入输出上限不变。查询只给一页时需按它的正式参数处理，本轮不新增通用自动翻页。

JSON直接交给现有`inputs`；自定义Python代码直接作为字符串传入。Skill复用已选择且已索引脚本的
原参数与输入结构。需要完整文件时，Python直接用`files`引用，不必先用`read_file`逐页读出再拼接。
Python成功发布和下载中心登记后的真实文件引用汇总到外层`generated_files` metadata，后续失败或
取消也保留已完成成果；下一Run按既有历史文件授权读取。文件正文与完整子调用结果不复制进metadata。

| 限制 | 固定值 |
| --- | --- |
| Monty内存 | 256MiB |
| 单个内部返回 | 32MiB，超过明确失败，不返回假全量 |
| 同Run REPL Session执行预算 | 120秒 |
| 每段run_code总截止 | 120秒，包含工具等待 |
| 每段内部调用 | 100次，失败同样计数 |
| Python/Skill inputs | 16MiB紧凑UTF-8 JSON，含JSON包装 |
| Python通信帧 | 64MiB，整包上限 |

MiB为1024×1024字节，和行数、Token不同；这些固定限制没有新增配置项。Python原64KiB代码、512KiB
正式结果、文件、日志、超时和内存限制不变。256MiB不是整个服务的内存保证；超限、超时明确失败，
先检查已完成副作用，再决定缩小范围或改为源端聚合，不自动重跑整段。

## 运行边界

完整使用约束集中在 `run_code` 工具说明中；工具搜索只提示发现后的工具可按原生 Schema 直接调用，发现通知只列出本次新增的名称。发现工具不要求进入 PTC。此安排不改变授权、调用参数、结果或暂停恢复协议，也不重写已有历史通知。

`run_code(code: str, restart: bool=false)` 使用 Monty Python 子集。支持 `await`、条件、循环、容器处理
和`asyncio.gather`，但没有宿主文件、环境变量、网络Socket、Shell、pip或第三方包。文件原样
下载经`load_file_data`，网页抓取经已装配的`web_fetch`，自定义处理经Python沙箱；各自安全限制不变。
工具参数和业务说明以原生Tool Schema为准，动态目录只列可用函数名。
动态目录中的函数只能写在 `run_code` 代码中；只有同一协议名也出现在普通工具列表时，才允许
作为外层工具直接调用。仅既有可信MCP返回Schema参与类型生成，其他MCP及新增builtin按JSON/Any处理。
当前使用官方AsyncMonty异步桥，继续复用SDK工具管理、校验、预算和返回转换。超时/取消停止
worker并取消、等待已启动宿主回调后清理；同Run正常复用REPL，restart清变量但不回滚副作用。
UI工具确认、暂停恢复和现有Calls界面不变。

## 本地开发

固定使用稳定基线：

```bash
PYTHONPATH=src .venv/bin/python -m deep_agent.combined_runtime \
  --local-config ~/.config/deep-agent/sqlite.env \
  --api-port 8000
```

数据库为 `data/deep-agent-local.db`，本地 FileStore 为 `data/deep-agent-local-files/`。不要为普通功能
测试新建空数据库，也不要把破坏性迁移测试指向该稳定库。

## 排障

在「诊断 → 会话 → 轨迹」选择 `run_code`，打开 `Calls` 可按内部调用逐步查看状态、耗时与有限的输入、结果和错误摘要。顶部“外部调用覆盖”是内部调用时间区间的并集；并行步骤不会重复计时，也不代表 CPU 时间。上方是不同颜色的调用耗时条，步骤较多时在此区域滚动；选中一步后，在下方切换 `Input / Result / Code`，失败步骤还可查看 `Error`。JSON 在详情内部滚动。超过 20 步时点击小文字「加载更多」。`Code` 复用本次 PTC 的原始源码：有可靠行号时标记该行，否则仅按函数名标记可能的调用位置。旧记录若无采集数据会显示提示，不影响其他页签。

这些摘要仅保存在会话事件 `ToolReturnPart.metadata.ptc_diagnostics`，不送回模型，也不会截断代码内传给下一工具的原始结果。诊断会脱敏、截断：单步输入和输出摘要各最多 8 KiB，单字符串 2 KiB，整次新增 JSON 128 KiB。涉及敏感内容时仍应避免把凭据写进代码或工具参数。诊断未能正常收尾会标记不完整。

历史合成案例和本轮验收应分开：会话是否存在以当前本地库为准，旧示例不是v0.14.7的回归证明。
本轮完整验收状态、真实Provider、浏览器及数据范围以单独测试报告为准；合成工具链路不等于线上业务回源。

- 看不到`run_code`：确认Agent已关联`builtin:ptc`；旧编码只用于兼容已有配置。
- 看不到MCP：确认已关联对应包且ToolSearch已披露；看不到Python/Web/Skill时检查原能力关联、配置、脚本索引和身份过滤，PTC不绕过这些条件。
- 文件变量无效：Token 只能在生成它的同一 Run 使用，且必须作为完整字符串参数传递。
- 文件过大或文本超限：Base64固定上限20 MiB；文本改用外层`read_file`分页读取。
- 工具别名冲突：碰撞工具会保留原生调用，日志只记录冲突名称，不进入PTC。

完整边界见[PTC设计](../design/20-code-mode/ptc-data-transfer-design.md)。
