# 三一 AI 助手（Agent）交接说明

> 交付物：**`agent-fb92e8ff-f9c6-4391-957e-86f24293e9b8.zip`（三一 AI 助手 Agent 整包）**，含提示词、4 个 Skill、能力清单与模型配置，导入即可还原整个 Agent。
> `sany-demo-workflows-v1.0.1.zip` 是其中业务 Skill 的单包（内容与整包内那份一致），单独提供便于只更新业务规则。
> 本文说明：这个 Agent 由什么组成、各服务分别接在哪里、以后改哪个文件、还有哪些坑。

---

## 1. 一句话概括

Agent 的「大脑」是平台上的提示词 + 4 个 Skill + 平台已注册的 MCP。本次交付没有业务代码改动，工作量集中在**提示词与 `SKILL.md` 的措辞**：让模型少发无效调用、把互不依赖的查询合批、失败时如实说明缺口。固定场景（`B_Elite38` 的「01. SP战规VS预算（销售额）」+ 对应 PDF）的结论为销售额预算比 SP 战规低 **81.81 亿元 / -13.4%**，可作为后续改动的回归基线。

---

## 2. 交付物与组件关系

### Test 环境入口

| 用途 | 地址 |
| --- | --- |
| Console（对话、看轨迹、Agent / 模型配置） | `http://epm-test.sany.com.cn/deepfos-server/deep-agent-server1-0/console/#/workspace` |
| MCP 注册与查看（`deepfos`、`wind-stock`、`wind-edb` 等） | `http://epm-test.sany.com.cn/deepfos-server/deepfos-mcp/install` |
| 后端 API 根（Skill 上传、会话、事件） | `http://epm-test.sany.com.cn/deepfos-server/deep-agent-server1-0/api/v1` |

调用时除 Cookie 外还需带 `X-Deep-Agent-App: qvwehc002`、`X-Deep-Agent-Language: zh-cn`（`/chat/stream` 另需 `app`、`space: qvwehc`、`language` 头）。

### 组件

| 组件 | 位置 | 版本 | 作用 |
| --- | --- | --- | --- |
| Agent 提示词 | Agent 包内 `agent.json` → `prompt`（自标 `sany-demo-v6`） | - | 总路由：加载哪个 Skill、PDF 先走 OCR、输出纪律、止损 |
| `sany-demo-workflows` | Agent 包 `skills/`（另附同内容单包） | 1.0.1 | 业务规则：战略 vs 预算核对、同行对标、普通查数 |
| `tmp-sany-ocr-http` | Agent 包 `skills/` | 0.3.4 | 集团私有化 OCR 异步接口适配（PDF → 完整文本 + 物理页码） |
| `wind-finance-data-http` | Agent 包 `skills/` | 0.1.0 | Wind MCP 的参数说明/字段口径参考（不含数据） |
| `kmp-knowledge` | Agent 包 `skills/` | 0.7.3 | KMP 知识库检索（制度、流程、内部依据） |
| MCP | 平台侧配置 | - | `mcp:deepfos`（表/Cube）、`mcp:wind-stock`、`mcp:wind-edb` |
| 内置能力 | 平台侧 | - | `builtin:document`、`builtin:general`、`builtin:ptc`、`builtin:sandbox`、`ui:builtin` |

整包由平台 0.15.4.rc 导出，含提示词、上表 4 个 Skill、能力清单与模型配置；导入后同编码为覆盖更新，可还原整个 Agent。包内 `sany-demo-workflows.zip` 与单包 `sany-demo-workflows-v1.0.1.zip` 内容一致（字节相同），两者任选其一，不要重复上传。

相对上一版，本次只更新了 `kmp-knowledge`（0.4.1 → 0.7.3：脚本由目录/清单/检索三个并为目录/检索两个，检索身份改为跟随调用者）；提示词、`sany-demo-workflows`、`tmp-sany-ocr-http`、`wind-finance-data-http` 均未变。

调用链（按一次「对比预算与战略目标」的实际顺序）：

```
用户消息 + context_refs(deeptable.page) + PDF 附件
  → Agent 提示词判定分支
  → load_capability(sany-demo-workflows)          业务规则进上下文
  → load_capability(tmp-sany-ocr-http)
  → execute_skill_script(run_ocr.py)              ← 打到集团 OCR 服务
  → deepfos_deeptable_workbook_get / cube_describe / cube_compare_data  ← 打到 DeepFOS MCP
  → read_file(OCR 报告, offset/limit)             按页码取原值
  → 组答：结论 + 重点表 + 缺口 + 下一步
```

---

## 3. 外部服务落点（每个 Skill 打到哪）

**这是维护时最需要知道的一张表。**

| 服务 / 数据源 | 谁在用 | 地址 | 凭据位置 | 挂掉时的表现 |
| --- | --- | --- | --- | --- |
| DeepFOS DeepTable / Finance_Cube | `mcp:deepfos`（在 `deepfos-mcp/install` 注册，不在包里） | 平台内部服务发现 | 平台登录态 | `deepfos_*` 工具报错/空结果，`deepfos_deeptable_workbook_get` 拿不到 Sheet |
| 集团私有化 OCR 文件解析接口 | `tmp-sany-ocr-http/scripts/run_ocr.py` | 测试 `https://agent-api-test.sany.com.cn`，生产 `https://agent-api.sany.com.cn`，前缀 `/ai-api/hehe/parser/hehe-file-tasks/` | Skill 包内 `.env`：`OCR_BASE_URL`、`OCR_AUTHORIZATION`（网关 API-KEY 原值，**不加 Bearer**，每请求带 `X-Request-ID`） | 脚本返回 `error`/业务码，模型应「说明缺项」而不是换服务 |
| Wind 金融数据 | `mcp:wind-stock`、`mcp:wind-edb` + `wind-finance-data-http` 说明书 | 平台已注册的 HTTP MCP（同样在 `deepfos-mcp/install` 查看） | 平台侧 | 工具报 isError / 鉴权额度错，模型应如实说失败 |
| KMP 知识管理平台 | `kmp-knowledge/scripts/{catalog,search}.py` | `http://ipaas-esb.sany.com.cn` | 网关 key 在包内 `.env`：`KMP_AUTH_KEY`、`KMP_BASE_URL`；**调用身份**来自宿主注入（`include_context: true`） | `auth_failed`（身份/授权）、`upstream_unavailable`（不可达）、`empty`（这批目录没检到），SKILL.md 有完整对照表 |

几个**实测确认、按直觉容易写错**的接口细节，已写进 `tmp-sany-ocr-http/references/http-api.md`，维护时优先看那份：

- `submit-file-task` 返回的是 `taskId`（UUID）；查询响应的 `data.task_id` 是**解析侧文档 ID（32 位 hex）**，两者本来就不同，拿后者当 `task_id` 查会 `taskId not found`。
- 轮询 `code=10702`（job not finished）时 `data` 里**没有** `parse_result` 键，要继续等，不是结构错误。
- 查询响应头是 `text/plain;charset=UTF-8`（gzip），body 仍然是 JSON，**不能靠 Content-Type 判断成败**。
- `page_start/page_count` 是页窗口，`total_count` 始终是总页数；拿到空 `parse_result` 即表示取齐。
- 导出 `export_type=json` 成功时 body 顶层是 `result.detail`，**没有** code/msg 包络；单次上限约 100 页。
- 平台文件中心**不收 .html/.md**，所以 markdown 导出交付为 `ocr-export-markdown.txt`。

KMP（`kmp-knowledge` 0.7.3）有两条硬前提，遇到的「查不到」多数出在这里：

- **身份**：调用脚本必须带 `resource_paths: [".env"]` 和 `include_context: true`。后者要求宿主在 `execute_skill_script` 时注入执行上下文（平台 0.15.6 起）；平台较旧或未注入时，脚本回退到包内 `.env` 的 `CONTEXT_USERNAME`——该值当前为空，于是直接返回 `auth_failed`。要么升级平台，要么在 `.env` 填一个服务账号（会覆盖调用者身份，权限随之变成该账号的）。
- **检索范围与开关**：`catalogId` 按**子树递归**检索，定位到「领域」这一级就够，不必到叶子目录；一次最多传 5 个目录，`limit` 默认 8。另外知识需单独开启「AI 高级功能」才会被检索到，所以「清单里能看到、检索却为空」是正常现象，不能据此判断知识库没有资料。

---

## 4. 先手动跑一轮，再导出轨迹看

改完 Skill 想知道有没有效果，最省事的办法是**手动跑一轮、把轨迹导出来看**。这套动作不需要额外工具。

### 4.1 手动跑一轮

1. 打开 Console（地址见 §2），进工作台，选 Agent「三一AI助手」和固定模型 `SY-Qwen3.8-Flash-Next`。**模型必须固定**，换了模型前后就不可比。
2. 从 DeepTable 报表页进入助手（或在 Console 确认当轮已带 `deeptable.page` 上下文）。没有上下文的对话拿不到 Sheet 和 Cube，等于白跑。
3. 上传 PDF 附件。附件必须是**当轮新上传**的，OCR 只认宿主上传后返回的 File ID。
4. 提问用固定句式（例如「帮我对比当前表的预算与战略目标，看看主要差距在哪里。」），这样多次运行才有可比性。
5. 边看边记：工具有没有按预期顺序调、哪一步报错、答完之后哪里对不上。

### 4.2 导出轨迹

同一会话切到右上「轨迹」页签（工作台是「对话 / 轨迹」两页签），点导出，得到一份

```
deepagent-trajectory-<会话ID前12位>.json
```

文件是脱敏的：`format = deepagent-session-trajectory`、`version = 1`、`source = history+sse`。文首 `limitations` 写明了它**不含**系统提示词、动态工具 Schema、认证 Header、Token / Cookie，历史轨迹也没有逐 Part 的精确耗时。业务数字、文件名称、工具参数都在里面，对外提供时按公司数据管理要求处理。

不想点页面也可以直接调接口拉同一份事件（`source` 会是 `history`，内容与导出等价）：

```bash
curl 'http://epm-test.sany.com.cn/deepfos-server/deep-agent-server1-0/api/v1/sessions/<session_id>/events?after_sequence=0&limit=500' \
  -H 'Cookie: <浏览器 Cookie>' -H 'X-Deep-Agent-App: qvwehc002' -H 'X-Deep-Agent-Language: zh-cn'
```

返回的 `items` 就是轨迹里的 `events`；按 `next_after_sequence` 继续翻页直到为空。

### 4.3 打开看哪几项

| 事件 / 字段 | 看什么 |
| --- | --- |
| `user/message` → `payload.context_refs[].label`、`payload.attachments` | 这一轮到底带的哪张 Sheet、哪个附件。带着「00. 编制说明」这种无数据页就是错的前提 |
| `tool/interaction` → `label` | 工具名（轨迹里工具名在 `label`，不在 `parts` 里） |
| `tool/interaction` → `payload.args` | 模型**真实传的参数**，最容易出问题的地方（数组被写成字符串、维度少传、表达式拼错） |
| `tool/interaction` → `payload.result` | 成功还是失败、返回了什么；失败常在 `result` 里以字符串形式出现 |
| `timestamp` / `timestamp_end` | 相减就是该工具耗时；相邻两条同秒出现说明是同一批并行调用 |
| `execution/error` | 模型服务 / Provider 级错误（如 HTTP 403），与 Skill 无关 |
| `assistant/message` → `payload.content` | 最终回答正文 |

判读时抓三件事，比总时长可靠：

- **批次**：两条工具调用相隔超过几秒，基本就是新一批（中间是模型往返）
- **无用调用**：重复 `workbook_get`、拿不到工具就转 `run_code` 兜底、参数类型报错重试
- **工具耗时占比**：工具本身只占几秒，总时长的大头不在工具；想变快只能减少调用批次，不是换工具

### 4.4 一段够用的分析脚本

把导出的 JSON 丢进去，直接打印时间线和每次工具耗时：

```python
import json, sys
from datetime import datetime

def when(x):
    return datetime.fromisoformat(x.replace("Z", "+00:00"))

d = json.load(open(sys.argv[1], encoding="utf-8"))
t0 = None
for e in d["events"]:
    ts = when(e["timestamp"])
    t0 = t0 or ts
    if e["event_type"] != "tool/interaction":
        continue
    end = when(e.get("timestamp_end") or e["timestamp"])
    args = str((e.get("payload") or {}).get("args"))
    name = str(e.get("label"))
    print("+%7.1fs  %5.1fs  %-38s %s" % ((ts - t0).total_seconds(), (end - ts).total_seconds(), name, args[:80]))
```

同秒出现多条 = 同一批并行；某一行耗时很长，再回去看它的 `payload.result`。

---

## 5. 以后要维护，改哪个文件

| 你遇到的情况 | 改这里 | 注意 |
| --- | --- | --- |
| 业务口径/规则变了（科目、版本、重点核对范围、缺数规则） | `sany-demo-workflows/SKILL.md` | 改完必须重新打包上传，**已安装的 Agent 不会自动更新**；改完跑一遍固定场景对比 81.81 亿 |
| 想换回答风格、字数、表格结构、下一步建议 | Agent `prompt`（`agent.json`） | 提示词和 Skill 是两层：提示词管「纪律」，Skill 管「业务判断」；同一条约束只在一处维护，两边各写一版容易冲突 |
| PDF 解析要改服务地址/超时/导出格式 | `tmp-sany-ocr-http/.env`、`scripts/config.py`、`SKILL.md` | `OCR_TIME_BUDGET_SECONDS` 上限 110s，超预算返回 `pending` 并保留 task_id，不会自动重提 |
| OCR 接口契约变了（字段、错误码、分页） | `scripts/client.py` + `references/http-api.md` | 先更新参考文档，再改脚本 |
| 增删查询工具 / 给 Skill 改编码 | Agent `capabilities`、`skills` + MCP 配置 | 换工具名要同步改 `SKILL.md` 里写的工具名与参数；Skill 改名或改编码要同步 Agent 的 `skills` 列表与提示词里出现的 skill_code |
| 同行对标取不到数 | `mcp:wind-*` 配置 + `wind-finance-data-http/references/*.json` | 那两份 JSON 是**安装时**的目录快照，只作参考，以在线 Schema 为准 |
| 制度/流程类问答 | `kmp-knowledge/SKILL.md`、`.env` | 检索账号默认跟随调用者（`include_context: true`）；`.env` 的 `CONTEXT_USERNAME` 只作兜底，填了会覆盖调用者身份。检索条数上限见 `KMP_SEARCH_LIMIT`（默认 8） |

维护流程（不需要额外工具，Console 里就能走完）。先判断改的是哪一层：

- **只改业务规则**（`sany-demo-workflows/SKILL.md`）→ 走 A，传 Skill 单包。
- **改提示词、能力清单、模型，或其它 Skill** → 走 B，重新导出并导入整包。

**A. 只更新业务 Skill**

1. 改 `SKILL.md`（包内 `SKILL.md` 就是源码），并把 `manifest.yaml` 的 `version` 递增。
2. 重新打包：`manifest.yaml` 与 `SKILL.md` 必须在 zip **根目录**，不要多套一层文件夹。
3. Console → Skill 页 → `sany-demo-workflows` → 上传 / 替换；或 `PUT /api/v1/skills/sany-demo-workflows/package`（multipart 字段名 `file`）。内容没变时返回 `noop`，属正常。
4. 回 Agent 确认该 Skill 仍在 `skills` 列表里。**已安装的 Agent 不会自动更新包内容**，靠这一步覆盖。

**B. 更新整包**

1. 在 Console 改提示词 / 能力 / 模型，或先改好各 Skill 源码。
2. Agent 详情页导出整包，得到 `agent-<agent-id>.zip`。
3. 在目标环境导入安装；同编码为覆盖更新。
4. **注意顺序**：整包会按包内容重装里面的每个 Skill。若同时更新过某个 Skill 单包，应「先导整包，再传最新 Skill 单包」，否则刚传的版本会被整包里的旧版本盖掉。

**C. 复测与回退**

同一张报表 + 同一份 PDF + 同一句提问，按 §4 导出轨迹，对比工具次数、批次和失败项，并人工核对结论数字是否仍为 81.81 亿元 / -13.4%。结论变了或指标没动就回退到上一版；一次只改一处，否则分不清是哪处生效。

---

## 6. 遗留问题与需要留意的点

1. **iFinD 是悬空引用**。`sany-demo-workflows` 和 Agent 提示词里都写了「缺项时读 `ifind-finance-data-http` 调 `get_security_indicators`」，但包内**没有**这个 Skill，Agent 也**没有** iFinD 的 MCP，真触发到这一步只会失败。要么补上对应的 MCP 与 Skill，要么删除该引用，避免模型做无效尝试。
2. **平台版本要求**。`kmp-knowledge` 0.7.3 的调用者身份依赖 `include_context`（平台 0.15.6 起），而 Agent 包由 0.15.4.rc 导出。目标环境低于该版本时，知识库检索会返回 `auth_failed`，需要升级平台，或在 `.env` 填服务账号兜底。
3. **凭据是明文**。OCR 的 `OCR_AUTHORIZATION`、KMP 的 `KMP_AUTH_KEY` 都以明文 `.env` 随包分发；导出包里已用占位值，实际部署要填真实值。联调用过的浏览器 Cookie / Token 不要写进文档、脚本或仓库，已外流的一律轮换。
4. **`parse_document` 与 OCR 的关系靠提示词维持**。业务 Skill 里仍写着「发现 `parse_document`」，OCR 优先是 Agent 提示词覆盖的；修改提示词时需保留这条优先级说明，否则 PDF 会回退到默认解析路径。
5. **单场景验证**。「通用性」目前只用同一工作簿的多张表 + 并行跑验证，未覆盖 Expense（费用）、Headcount 等其他预算表；新表上线前建议按 §5 流程再跑一轮。
6. **回答里不要出现业务判断词**。「未查到」不能写成「未编制」「未落地」，这条已写进提示词，属于业务敏感表述。
