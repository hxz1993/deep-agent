# 浏览器与页面操作设计

## 1. 结论

“操作当前业务页面”和“自主浏览网站”是两种产品能力，不应合并成一套工具：

```text
当前 DeepFOS 页面操作
  → 已知业务动作和页面内存状态
  → UI Tool / ExternalToolset / Deferred
  → 当前用户浏览器执行

外部或未知网站浏览
  → 开放目标和未知页面结构
  → Harness BrowserUse / browse_web(goal)
  → 隔离 Chromium 中的浏览器子 Agent 执行

Provider 原生 Computer Use
  → 显式启用的供应商专属能力
  → Provider 返回计算机动作或执行其原生能力
  → 必须按 Provider 单独接入和治理
```

当前主线只实现了第一类能力的静态版本：代码或数据库定义 UI Tool，模型调用后进入
Deferred/Pending，浏览器提交结果，再恢复模型。后两类能力均未在 DeepAgent 中启用。

## 2. 三类能力的准确含义

### 2.1 当前页面业务操作

执行对象是用户当前打开的 DeepFOS 页面，包括当前标签页的前端内存、选区、未保存状态和业务组件。

典型动作：

- 读取当前选区。
- 打开成员选择器或结构化确认表单。
- 将确认结果写回当前工作簿。
- 定位、高亮或刷新某个业务对象。

推荐链路：

```text
业务页面准备确定性的工具定义
→ DeepAgent 通过 ExternalToolset 暴露给模型
→ 模型调用工具
→ Run 进入 Deferred/Pending
→ 当前页面按业务 API 执行或等待用户提交
→ deferred_tool_results 恢复模型
```

这种能力的核心不是“模拟点击”，而是调用页面已经拥有的业务 API。它可以直接访问准确的
`sheet_id`、`range`、写入范围和组件状态，稳定性高于视觉自动化。

### 2.2 Harness 0.26 `BrowserUse`

Harness `BrowserUse` 是 Pydantic AI `AbstractCapability`。它向主模型增加一个固定工具：

```text
browse_web(goal: string)
```

主模型只描述一个自包含目标，独立的 browser-use 子 Agent 再用自己的感知—动作循环驱动真实
Chromium。该循环会索引 DOM、读取截图、规划、点击、输入、翻页和自我修复，最终把文本或经
Pydantic Schema 校验的结构化结果返回主模型。

它适合：

- 在外部网站查找资料或价格。
- 在未知管理后台定位配置。
- 跨页面完成结构不稳定、需要视觉判断的流程。
- 无稳定 API 的第三方门户操作。

它不适合替代：

- 当前 DeepFOS 页面中的选区、未保存数据或弹框状态。
- 已有确定性业务 API 的写回、确认和对象选择。
- 必须由当前用户在原标签页继续操作的流程。

固定版本源码确认的关键行为：

- 默认每次 `browse_web` 使用独立浏览器会话；也可配置 Agent 级共享会话。
- 可本地启动 Chromium，也可通过 `cdp_url` 连接远程浏览器。
- 默认阻止直接 IP 和 localhost 风格地址；支持域名白名单。
- 默认限制最大步骤，每个步骤通常会产生一次子 Agent 模型请求。
- 支持截图视觉、敏感信息占位和结构化最终结果。
- 默认排除文件读取和上传动作。
- 调用结束后清理自己启动的浏览器；共享会话需要显式 `aclose()`。
- 当前 DeepAgent 安装了 `pydantic-ai-harness[skills,exa,youdotcom]==0.26.0`，没有安装可选的
  `pydantic-ai-harness[browser-use]`/`browser-use` 运行依赖，因此该能力当前不可运行。

### 2.3 Provider 原生 Computer Use

Provider 原生 Computer Use 是供应商协议能力，不会因升级 Pydantic AI 自动出现，也不会自动
获得当前用户浏览器。以 OpenAI Responses 为例，应用必须显式将 `computer_use` 加入
`openai_native_tools`，并使用支持该能力的模型。

升级到Pydantic AI 2.35.1不会自动启用Computer Use。DeepAgent当前没有配置对应模型、动作执行器、
History和安全边界，因此不能只升级依赖就获得完整可用的能力；若未来采用，应把具体Provider的
动作循环作为独立能力接入。

它比 Harness `BrowserUse` 更底层、也更依赖 Provider：

- 工具格式、动作回传和支持模型由 Provider 决定。
- 不同 Provider 的能力与安全边界可能不同。
- 不能作为所有 OpenAI-compatible 端点共有的通用协议能力。
- 它没有天然提供 DeepFOS 当前页面的业务对象和前端内存状态。

因此 Provider Computer Use 只能在某个供应商能力成为明确产品需求后单独设计，不进入
DeepAgent 默认工具目录。

## 3. 能力对比

| 维度 | 当前页面 UI Tool | Harness `BrowserUse` | Provider Computer Use |
| --- | --- | --- | --- |
| 操作对象 | 用户当前打开的 DeepFOS 页面 | 独立或远程 Chromium 中的网站 | Provider 支持的计算机环境 |
| 主模型看到的工具 | 具体业务工具 | 一个 `browse_web(goal)` | 显式配置的 Provider Native Tool |
| 谁规划每一步 | 主模型选择业务动作，页面确定性执行 | 浏览器子 Agent 自主规划 | Provider 模型与宿主执行循环 |
| 页面知识 | 准确业务 ID、选区和组件状态 | DOM、截图和页面文本 | 截图/动作等 Provider 协议数据 |
| 登录状态 | 天然是当前用户页面 | 需单独 Profile、Cookie 或 CDP | 取决于 Provider/宿主环境 |
| 成本 | 通常一个 Deferred 工具循环 | 可能数十次子 Agent 模型请求和截图 | 依 Provider 计费与动作轮次 |
| 稳定性 | 业务 API 级 | 易受页面结构、弹框和提示注入影响 | 依 Provider 和计算机环境 |
| 前端接入 | 必须 | 通常不需要业务前端 | 依宿主执行方式 |

## 4. 远端 `feat/web-capabilities` 审查

### 4.1 已确认事实

远端分支当前 HEAD 为 `b12e683`，相对共同基线包含 4 个提交、76 个文件，约新增
15,332 行。它不是通用 Browser Use，而是“请求级浏览器工具声明 + 安全续跑原型”。

其请求体新增：

```text
web_capabilities
web_continuation_binding
```

`web_capabilities` 包含：

- 当前页面上下文 ID 和内容 revision。
- 页面提供的 Skill 说明。
- `web__*` 工具名、版本、描述和输入/输出 Schema。
- client 执行方式和风险等级。

后端把声明转换为请求级 `ExternalToolset`。工具没有服务器处理函数，调用后通过 `CallDeferred`
交回浏览器；风险为 `approve` 的工具被后端转成 Pydantic AI 未审批工具，避免只依赖前端自觉审批。

### 4.2 值得保留的思想

- 页面工具使用独立、稳定的 `web__` 命名空间。
- 动态声明只对当前请求和当前页面上下文有效，不写入全局工具注册表。
- 工具仍复用 Pydantic AI `ExternalToolset` 和 Deferred，而不是自研模型工具状态机。
- 页面传入的 Schema、大小、命名和 revision 必须在服务端校验。
- 高风险操作必须由服务端强制审批，不能把 `risk` 当装饰字段。
- 页面结果恢复时需要绑定正确用户、会话、页面上下文和工具调用。

### 4.3 不应整体合并的部分

该分支同时加入了：

- Web continuation ledger 和 runtime state。
- HMAC continuation token。
- Session lease、generation/fencing 和历史 anchor。
- Principal/tenant 作用域扩展。
- 5 个旧迁移 `021`～`025`。
- 大量 Runner、持久化和会话执行改造。

当前主线数据库基线已经重整为 `001/002`，不能直接接受这些旧迁移。更重要的是，分支内的
`web_continuation_preparation.py` 明确只是供未来传输层使用的内部准备器，当前没有公开 Router，
说明完整 continuation 产品链路尚未闭环。

内部页面工具目前不需要为极端的页面热更新、多 Worker 重放和跨进程续跑一次性引入整套 Ledger、
HMAC、Lease 和 Fencing。整体 cherry-pick 会同时带回旧 Runner 和持久化模型，风险远高于收益。

## 5. DeepAgent 推荐架构

### 5.1 DeepFOS 页面操作：沿用 UI Tool

当前确定性业务动作继续使用：

```text
UI Tool
→ ExternalToolset
→ Deferred/Pending
→ 当前页面执行
→ deferred_tool_results
→ 模型恢复
```

如果未来多个业务页面确实需要“按当前页面动态声明工具”，再在现有聊天请求上增加一层很薄的
请求级页面能力清单，首版只做：

- 严格公共外壳和 JSON Schema 限制。
- `web__` 命名空间。
- 当前请求、用户、会话和页面上下文绑定。
- 调用后复用现有 Deferred 结果恢复。
- 服务端风险下限和必要审批。

首版不预设：

- 独立 continuation ledger。
- 新的聊天事实源。
- HMAC bearer continuation token。
- Session lease/fencing。
- 多张 Web 专用状态表。

若未来出现“浏览器关闭后由另一个进程继续”“页面实现更新后恢复旧 Pending”或真实重放攻击证据，
再按对应问题增加最小状态，不提前复活完整旧分支。

#### 即时动作与人工交互必须分流

旧分支把浏览器执行都收敛为 Deferred，协议统一但产品语义偏重。更合理的方式是按动作是否需要
跨时间等待分成两条链路。

即时页面动作，例如读取选区、定位元素、刷新预览：

```text
模型调用 web__get_current_selection
→ DeepAgent 经当前 SSE/WebSocket 下发 page_action_request
→ 当前页面立即执行 JavaScript/业务 API
→ 页面按 call_id 回传结果
→ 同一个 Agent Run 内继续模型循环
```

实现上可以继续使用 SSE 下行 + 独立 POST 上行，也可以使用 WebSocket。关键不是传输协议，而是
后端 Toolset 等待一个短时结果 Future；成功后形成普通 Tool Result，不结束当前 Run，也不创建
Pending 记录。

需要用户操作的动作，例如成员选择、表单确认、写入审批：

```text
模型调用 web__open_member_selector
→ DeepAgent 返回 Deferred/Pending
→ 当前页面渲染组件并等待用户
→ 用户提交 deferred_tool_results
→ 新请求恢复模型
```

这种场景可能等待几十秒、几分钟甚至页面刷新，Deferred 才是正确原语。不能为了减少一次请求，
长期占住模型 Run 和服务器连接。

第一版还应避免每轮重复传完整能力定义。页面打开或上下文变化时注册/发送一次 Manifest 和 revision，
聊天请求只绑定当前 `web_context_id + revision`；后端使用短生命周期内存或请求快照，不默认落数据库。
多 Pod 下的即时结果必须命中原 Run，需沿用会话粘性，或未来增加轻量共享 Action Broker；这与需要
长期持久化的 Continuation Ledger 仍是两个级别的问题。

#### 截图不是默认结果

确定性业务动作通常只需结构化 JSON，不应每次附带截图。只有操作依赖视觉状态或需要模型复核时，
页面才按工具契约返回裁剪截图、可访问性快照或视觉摘要。截图属于某次工具调用的 Observation，
不属于 Capability Manifest，并需要大小、权限和敏感信息限制。

#### 协议通用，工具不通用

`Web Capabilities` 可以成为任何 DeepFOS 页面都能接入的通用协议，但每个工具仍由具体页面实现：

```text
通用协议：名称、Schema、风险、call_id、结果和生命周期

页面专属工具：
- DeepTable：web__get_current_selection、web__fill_cells
- DeepCube：web__open_dimension_selector、web__apply_cube_config
- Dimension：web__locate_member、web__preview_formula
```

没有实现对应 handler 的页面不能执行该工具。若统一提供 `click(selector)`、`type(selector, text)`、
`screenshot()` 等任意页面工具，确实会更“通用”，但它就变成了 Playwright/BrowserUse 风格的
浏览器自动化，失去业务 API 的准确性和安全边界，不应混在页面业务工具协议中。

### 5.2 外部网站：独立 BrowserUse 能力

只有出现明确外部网站任务时，才把 Harness `BrowserUse` 作为独立、默认关闭的能力接入。建议
第一版边界：

- 只允许只读浏览和信息提取。
- 使用隔离 Chromium 或受控远程 CDP，不连接用户当前浏览器。
- 不传当前用户 Cookie，不复用 DeepFOS 登录态。
- 必须配置域名白名单，保持 IP/localhost 阻断。
- 禁止文件上传、下载执行和内部网络访问。
- 每次调用使用独立会话并在 `finally`/`aclose()` 中清理。
- 限制最大步骤、单步超时、子 Agent Token 和总调用时间。
- 子 Agent返回网页内容一律视为不可信数据，防止网页 Prompt 注入。
- 通过现有观测主线记录子 Run ID、步骤数、用量和终态，但不记录 Cookie、截图敏感内容或完整网页。

BrowserUse 是 Harness `AbstractCapability`，并不天然等于一个现有数据库 ToolPackage。正式接入前
要先决定 Agent 配置如何选择它：推荐给管理员呈现为独立能力项，运行时由该项启用 Capability；
不要为了满足“一包一 Toolset”而伪装成普通 MCP。

### 5.3 Provider Computer Use：保持显式、按供应商试点

只有某个 Provider、模型和运行环境已经确定时才做试点。试点必须明确：

- 支持模型和 Provider 专用设置。
- 谁执行鼠标、键盘和截图动作。
- 域名/网络边界、登录态和敏感信息。
- 取消、超时、用量、审计和会话清理。
- 对普通 OpenAI-compatible、DeepSeek、Anthropic/Google 的回退行为。

不允许把它作为“所有模型都有的内置浏览器工具”默认启用。

## 6. 与 Context Reference、UI Deferred 和 Output 的关系

- `context_refs` 描述当前页面事实，不执行页面动作。
- UI Tool 执行页面动作并等待浏览器/用户结果，属于 Deferred。
- BrowserUse 返回独立浏览器子 Agent 的调查结果，通常是普通 Tool Result，不要求当前页面恢复。
- Output Tool 只负责最终结构化结果和 Run 终止，不执行页面交互。
- 需要先与页面交互再给结构化最终结果时，顺序应是 UI Deferred 完成后，再由模型生成最终输出。

## 7. 当前决策和触发条件

| 方向 | 当前状态 | 重新评估条件 |
| --- | --- | --- |
| 静态 UI Tool + Deferred | 已实现，继续使用 | 按现有缺陷和业务需求维护 |
| 请求级动态页面工具 | 有潜力，暂不排期 | 至少两个业务页面需要动态声明，且静态 UI 包无法合理配置 |
| Harness `BrowserUse` | 有潜力，暂不排期 | 出现明确外部/未知网站任务和可接受的安全、资源预算 |
| Provider Computer Use | 有潜力，暂不排期 | 某个 Provider 能力、模型和执行环境成为明确产品需求 |
| 整体合并 `feat/web-capabilities` | 已关闭 | 不重新打开；只允许按真实问题提取单个设计思想 |

## 8. 实施前验证清单

### 页面动态能力

- 页面声明无法扩大 Agent 原有授权或跨用户/会话复用。
- 非法 Schema、重复工具名、超限内容和伪造风险等级被拒绝。
- Deferred Pending、刷新、结果恢复、取消、ModelRetry、SSE 和 History 一致。
- 页面刷新或上下文改变时，旧 Pending 明确失败而不是误执行。
- 写入动作在业务服务再次校验对象、范围、权限和当前状态。

### BrowserUse

- 未安装可选依赖时，能力不可选择且错误可诊断。
- 域名白名单、IP/localhost 阻断和重定向限制有效。
- 每次调用 Browser/Context/子 Agent 均可靠清理。
- 取消可终止子 Agent 和 Chromium，不遗留后台进程。
- 步骤、时间、Token 和并发上限生效。
- 网页提示注入、跨域敏感数据和文件上传被阻止。
- 普通工具、历史、压缩和 Console 对长时 BrowserUse 调用表现一致。

## 9. 证据入口

- 远端分支：`origin/feat/web-capabilities@b12e683`
- Harness [`BrowserUse` 文档](https://github.com/pydantic/pydantic-ai-harness/blob/main/docs/browser-use.md)
- Pydantic AI [OpenAI 原生工具文档](https://github.com/pydantic/pydantic-ai/blob/main/docs/models/openai.md)
- OpenAI [Computer Use 动作循环](https://developers.openai.com/api/docs/guides/tools-computer-use)
- Anthropic [Computer Use Tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)
- Microsoft [Playwright MCP](https://github.com/microsoft/playwright-mcp)
- 当前 [UI 工具指南](../../guides/ui-tools-external.md)
- 当前 [UI 工具设计历史](../01-ui-tools/ui-tools-history.md)
- 当前 [框架能力采用台账](../14-framework-capabilities/framework-capabilities-baseline.md)
