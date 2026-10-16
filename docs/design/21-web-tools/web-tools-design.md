# DeepAgent Web 工具设计

> 2026-09-19 更新。搜索改版状态：**v0.10.14 已实现**。
> 本文保留2026-09-19的历史实施设计。2026-10-16已迁移DeepSeek独立Search并支持两家Fetch；当前行为见[使用与API指南](../../guides/web-tools.md)，下文的Messages及Flash/Pro选择不再是当前实现。不代表已部署环境已经升级。

## 改版结果

统一向已授权 Agent 提供普通函数工具 `web_search(query)`，由全局配置指定搜索服务。
取消“会话模型原生搜索”产品模式，主会话模型及其协议保持不变。搜索服务与主会话模型解耦，
不按主会话型号推断搜索可用性，不自动选择供应商，也不隐式降级。

| **项目** | **改版前** | **当前本地实现** |
| --- | --- | --- |
| 空配置、旧 `native` | 默认或继续走当前模型搜索；Kimi 有普通工具特例 | 统一未配置；仍披露搜索工具，实际调用时返回失败结果，会话继续 |
| DeepSeek 独立搜索 | Pydantic AI 辅助 Agent + Responses，默认 V4 Pro | 异步直调 Anthropic Messages，默认 Flash；只提取搜索来源与引用片段 |
| Kimi 独立搜索 | 辅助模型生成搜索调用，执行 Search API，再由辅助模型总结 | 直接调用 Search API，不使用辅助模型，不再读取 `model` |
| 控制台 | 原生 / 指定供应商两模式 | DeepSeek / Kimi / 网络搜索服务商三段切换及“暂不配置”文字选项 |
| 存储 | `global_settings.web_search`，结构版本 1 | 复用同一行和现有 Provider 结构；无配置时不生成默认搜索服务 |

“不再提供会话原生模式”是本次产品决策，不代表供应商不具备服务端搜索能力。DeepSeek 独立搜索
内部仍使用服务端原生搜索，但从主会话看是一个普通工具。主会话全面切换 Anthropic 协议不在本次范围。

## 产品与授权

内置工具包默认不关联：

```text
builtin:web
├── web_search(query)
├── web_fetch(url)
└── web_image_view(url, force_download=false)  # 仅图片输入模型
```

只有显式关联 `builtin:web` 的 Agent 才获得网络工具。全局搜索设置不能替代 Agent 授权。
未配置搜索服务时，仍注册 `web_search`，不因缺配置在模型装配或 Run 开始前拒绝会话；只有实际
调用搜索时才返回配置缺失结果。`web_fetch` 与图片读取不依赖搜索配置，仍可正常工作。
Internal HTTP、任意 HTTP Request、Python/Monty 任意联网不在本课题范围。

## 整体链路

```text
用户问题 → 主会话模型生成 query → 普通函数 web_search(query)
  ├─ 未配置：返回失败工具结果 → 主模型解释原因或继续处理其他任务
  ├─ DeepSeek：异步 Messages + 服务端搜索 → 提取来源、标题、引用片段
  ├─ Kimi：异步 /tools/search → 结构化搜索结果
  └─ 第三方：现有 Common Tools / Harness 搜索适配 → 搜索结果
搜索结果 → 同一主会话模型生成最终回答
```

只有 DeepSeek 路径包含一次独立的辅助模型请求；不创建额外 Pydantic AI Agent。
Kimi 与第三方直接调用检索服务，不增加辅助模型生成搜索词或总结答案的往返。

## 数据与历史兼容

复用现有通用配置表，不新增搜索 Provider 表，不为三类界面入口增加存储层级：

```text
global_settings(key PK, value TEXT JSON, version SMALLINT, created_at, updated_at)
key = web_search; version = 1
```

新部署初始化完成后没有 `web_search` 默认配置行；旧 `{"mode":"native"}`、无记录和空配置
都归一为未配置。读取不写库，不自动迁移成某个供应商，不再保留旧原生执行分支。
旧合法 `mode=provider` 保持供应商、凭据与显式配置。三个界面入口只负责分组，仍保存具体 Provider：

```json
{"mode":"provider","provider":"deepseek","credential_source":"llm_provider","llm_provider_id":"<provider-id>","model":"deepseek-flash"}
```

```json
{"mode":"provider","provider":"kimi","credential_source":"api_key","api_key":"<仅写入请求使用的密钥占位>"}
```

```json
{"mode":"provider","provider":"tavily","credential_source":"api_key","api_key":"<仅写入请求使用的密钥占位>"}
```

- DeepSeek 保留 `model`。新配置默认 `deepseek-flash`，直接展示模型选择，可选择已实测的 Pro；历史显式模型
  不静默替换。界面候选列表是推荐，不作为运行时型号硬限制。默认模型不等于自动生成搜索配置。
- Kimi 忽略旧 `model`，读取时不回写；用户下次保存时删除这一无效字段，保留 API Key 和 Provider 引用。
- DuckDuckGo 虽不需要 Key，也必须显式选择并保存，不作为空配置的自动后备。
- 前端把空记录、空配置和旧 `native` 显示为“未配置”，弹框选中“暂不配置”。
- 选择“暂不配置”后保存，提交 `{"mode":"unconfigured"}`，将原搜索配置替换为 `{}`，清除搜索凭据和引用；不会删除被引用的模型 Provider。允许再次配置；不改变未配置时的工具失败结果语义。
- 新保存接受有效的供应商配置或显式 `mode=unconfigured` 清空请求，不继续保存 `native`。未配置的管理响应为 `value.mode=unconfigured`，配置有效时为 `provider`；
  失效引用通过 `configuration_error` 回显以便修复，Console 已同步，聊天 SSE/History 协议不变。
- 配置备份恢复也必须保留同一语义，恢复旧 native 不能重新激活原生搜索。

已发布 `001` 迁移保持不变。全新数据库迁移到 Head 时，`db/initial_configuration.py` 的
`initialize_current_defaults()` 删除该基线生成的搜索种子行；该函数仅由空库初始化路径调用，
历史库升级及重启不删除配置，不新增迁移或 DDL。运行时仍将旧 native 视为未配置，兼容历史备份恢复。
隔离 SQLite 已验证空库无默认行、旧库配置保留。回滚旧程序可能重新把空配置解释为 native，语义并不等价。

## 凭据与请求地址

DeepSeek、Kimi 都继续支持“沿用已配置 Provider”和“单独 API Key”。复用时仅保存 `llm_provider_id`，
每次执行从受信记录解析最新 Key，不复制密钥。独立 Key 沿用掩码回显、同 Provider 留空保留、切换不继承规则。

- DeepSeek 搜索的默认地址为 `https://api.deepseek.com/anthropic/v1/messages`，复用 Key 不意味着可照搬
  聊天的 Chat Completions / Responses 地址。自定义代理必须明确支持 Messages 路径；不静默绕过代理、
  切换域名或把 Key 发往猜测出的地址。地址适配失败作为搜索工具失败结果返回。
- Kimi 沿用已选 Provider 的国内/国际基址，在相应基址请求 `/tools/search`；独立 Key 模式使用明确的
  官方默认基址。错误中不泄露 Key、Authorization、Cookie 或完整鉴权响应。
- 被引用 Provider 删除、Key 缺失或配置失效，不阻止整个 Agent 装配；实际搜索调用时才产生失败结果。
  管理保存仍校验引用、字段与凭据完整性。

## 供应商执行

### DeepSeek

单独封装异步请求函数，不构造辅助 Agent、不维护额外会话循环。使用现有异步 HTTP 基础设施处理
超时、取消、响应校验和脱敏。默认请求骨架：

```text
POST https://api.deepseek.com/anthropic/v1/messages
x-api-key: <服务端解析的凭据>
anthropic-version: 2023-06-01
Content-Type: application/json
```

```json
{
  "model": "deepseek-flash",
  "max_tokens": 4096,
  "messages": [{"role": "user", "content": [{"type": "text", "text": "Perform a web search for the query: <query>"}]}],
  "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]
}
```

这是经 DSH 源码和直连验证的协议骨架；预算可以在有证据的前提下缩减，不以输出截断换取伪成功。
解析 `web_search_tool_result` 中真实的 `web_search_result`，取得 URL、标题、时间，并按 URL 合并
文本引用中的 `cited_text`。结果按 URL 去重并沿用最多 5 条的产品输出上限。
辅助模型生成的整段回答不作为工具正文返回，主会话只接收整理后的来源和引用片段；完整网页由 Fetch 获取。

HTTP 200、模型声称“已搜索”、普通文本中的 URL 都不能单独判为成功。缺少搜索结果块、搜索错误块、
无有效结果、鉴权/限流/超时均返回有原因的失败 Tool Result。真实搜索存在也不等于网页内容已经事实核验。
调用失败不新增型号黑名单，不自动改模型、协议或供应商。

### Kimi

直接异步调用官方检索服务，删除辅助 Chat 模型往返：

```text
POST /v1/tools/search
```

```json
{"text_query":"<query>","limit":5,"timeout_seconds":30,"include_content":false}
```

复用配置的 API Key，校验 `search_results` 及合法结果 URL，直接归一化后返回主模型。
请求没有 `model`，控制台不显示模型选择。Formula、`$web_search`、Responses 和辅助 Chat 循环
都不作为本次独立搜索后备路径。

### 第三方搜索

DuckDuckGo、Tavily 继续复用 Pydantic AI Common Tools；Exa、You.com 复用 Harness。
保持直接检索、结果上限与来源归一化，不新增辅助模型。空结果应明确返回“无结果”，不能被测试界面
笼统包装成“真实搜索成功”；不伪造来源，也不自动切到别家搜索。

## 工具失败结果与事件（本次硬约束）

用户要求的是“调用发生并留下结果，搜索业务失败”，不是“配置有误就中断整个会话”。
实际调用前仍正常披露工具，调用后必须保留同一 `tool_call_id` 的 Call / Result 配对。
复用现有 Pydantic AI `ToolFailed → ToolReturnPart(outcome="failed")` 和事件映射，不将普通搜索失败
提升为 Run 级终止异常，也不标记成搜索成功。若内部使用异常实现，必须在工具执行层收口为结果。

未配置时的现有 SSE 字段示意（内容示例，不新增事件类型）：

```json
{"type":"tool_call","tool":"web_search","args":{"query":"今日新闻"},"tool_call_id":"search-1"}
{"type":"tool_result","tool":"web_search","result":"网络搜索失败：未配置具体的搜索供应商。请在全局配置中配置网络搜索服务后重试。","tool_call_id":"search-1","outcome":"failed"}
```

随后模型继续处理，可能解释“网络搜索工具调用失败，当前未配置供应商”，也可处理不依赖搜索的部分。
不能保证模型逐字复述，但它必须收到具体失败原因。缺配置不消耗供应商请求，也不通过 `ModelRetry`
让模型反复调整 query。用户主动取消、基础设施故障、主模型自身失败仍保持既有终止规则。

Event Store 保存真实失败 Tool Return；SSE、History 和 Console 工具卡沿用现有失败展示。
刷新页面后仍能看到调用和失败原因。不得吞掉事件、返回空字符串、伪造成功结果或仅弹前端 Toast。
Web 搜索不进入 PTC；不伪造未发生的工具调用或嵌套审计。

| **情况** | **搜索工具结果 / 管理行为** |
| --- | --- |
| 未关联 `builtin:web` | 不披露网络工具 |
| 无配置、空配置、旧 native | 实际调用时返回未配置失败结果；Run 继续 |
| 已存配置引用失效、缺 Key、搜索地址不兼容 | 实际调用时返回具体失败结果；Run 继续 |
| 搜索鉴权/限流/超时/无执行证据 | 脱敏失败结果；Run 继续，无隐式降级 |
| 管理员保存不完整配置 | 保存接口字段校验失败，不写入坏配置 |
| 管理员测试搜索失败 | 测试响应 `ok=false` 和原因；不保存配置、不创建会话 |

## 管理接口与 Console

沿用企业管理员门禁与配置入口：

- `GET /api/v1/global-settings/web-search`：空态为 `unconfigured`；失效配置回显可解析的 Provider/引用和
  `configuration_error`，不因配置错误返回 500，读取不修改数据库。
- `PUT /api/v1/global-settings/web-search`：要求显式 `mode=provider`，不接受 native 或空草稿；
  校验供应商和凭据，忽略并清理 Kimi 旧 model。
- `POST /api/v1/global-settings/web-search/test`：测试未保存草稿，返回现有 `ok/message/elapsed_ms` 等字段。
- `/test-model` 返回 HTTP 410 和已移除提示；Console 移除其入口，不改成测试其他供应商。

编辑弹窗提供 DeepSeek、Kimi、网络搜索服务商三段切换及“暂不配置”文字选项。DeepSeek 默认 Flash，直接展示搜索模型；
Kimi 无模型字段；第三方入口再选择 Tavily、Exa、You.com 或 DuckDuckGo。DeepSeek/Kimi 复用现有
“沿用 Provider / 单独 API Key”控件；自定义下拉沿用 Console 组件，不增加一套选择器。

测试必须调用实际搜索路径，不只检查 Key 非空或模型能回答：第三方/Kimi 直接发一次短查询；DeepSeek
使用明确要求搜索的短问题，验证结构化结果。取得足够结果即可结束可终止的请求，不要求主模型总结。
真实耗时包含检索、网络与 DeepSeek 服务端模型时间；不承诺所有供应商几百毫秒返回。

### Web Fetch与图片读取


复用Pydantic AI 2.35.1本地WebFetch和SSRF-safe downloader：仅HTTP(S)，DNS解析后连接固定IP，
每次重定向重新解析校验，禁止回环、私网、链路本地与云元数据；DeepAgent额外禁止URL内嵌凭据，
不添加Cookie、Authorization或平台Header。下载上限5 MiB，文本上限50K字符，超时30秒；二进制
只接受PDF和常见图片。直接抓取二进制图片时继续返回Pydantic AI原生`BinaryContent`，并继续受现有
ToolOutputLimits规则约束。

Pydantic AI 2.35.1的WebFetch把HTML交给`markdownify`时固定跳过`img/script/style`，没有公开
`strip`配置。DeepAgent不修改`site-packages`，只在本仓保留一层HTML转换薄适配：继续复用上游
`safe_download`、SSRF、重定向复检、内容类型和长度限制；显式丢弃`script/style`正文，把`img`
保留为原位置的`![alt](绝对HTTP(S) URL)`。相对URL和协议相对URL按最终页面地址规范化，
`javascript:`、`data:`和带凭据地址不进入Markdown。

`web_image_view`是图片输入模型的外层普通工具。它先执行公网URL/SSRF校验，再返回原生
`ImageUrl`：`force_download=false`允许Provider直接消费URL，Provider不支持时由Pydantic AI适配；
`force_download=true`要求Pydantic AI在组装下一次模型请求时安全下载并转成Provider视觉参数。
工具结果本身不含图片字节。无扩展名URL用既有有界WebFetch路径探测MIME；非图片、私网、回环、
云元数据和凭据URL明确拒绝。

系统不自动发送页面前三张或任意预选图片。模型先从Markdown中的位置、alt与正文关系判断需要查看
哪些图片，再调用一次或并行调用多个`web_image_view`。

部署时应为Web Fetch提供可控的公网出口。无代理时直接沿用Pydantic AI的系统DNS与固定IP连接；
显式HTTP CONNECT代理下，通过公共DNS获得待校验和固定的公网IP，并把原始主机名传递给TLS SNI，
兼容Clash类fake-IP环境。兼容钩子在进程内保持安装，但只在存在显式HTTP隧道代理时生效，因此既覆盖
`web_fetch`执行期，也覆盖工具返回后Pydantic AI为`ImageUrl(force_download=true)`组装下一次模型请求
时发生的延迟下载。公共解析不可用时回退原解析并继续执行全部SSRF校验；不得改回普通URL直连、
关闭证书校验或绕过重定向复检。

## Code Mode、安全与审计

搜索统一作为普通函数，只在 Agent 授权 `builtin:web` 时披露；Web 工具沿用 v0.6.7 起的边界，
不进入 PTC/Monty，也不开放内部 Socket、任意 Header 或平台凭据。`web_image_view` 仅供视觉模型外层调用。
搜索结果保留 Provider、Query、来源与不可信外部内容标记；请求凭据不进入 Event Store。
不再通过 Web 配置向主会话注入 Native Search Capability，历史已保存的 Native Tool Part 仍可正常回读。

当前`agent.run()`的`web_image_view`结果保持`ImageUrl`，供框架组装紧随其后的模型视觉请求；Event
Store只保存URL、MIME、`force_download`和SSRF审计，不保存Base64或文件。下一次独立Run加载历史时，
旧图片结果投影为轻量URL说明，避免每轮重复发送图片。SSE、Conversation History和轨迹使用同一
安全引用；Console仅按URL懒加载无Referrer预览，失败时保留原图链接，不进入平台附件存储。

## 验收与实施边界

本节是验收约束；实施状态统一登记在 [研发台账](../TODO.md)，不在本课题维护另一份任务清单。

- 全新数据库初始化后无默认搜索行；无记录、空配置、旧 native 均显示未配置，读取不回写。
- 授权 Web 且缺搜索配置时，模型仍能看到工具；不调用则正常回答，调用后保留失败 Call/Result，模型继续并生成最终回答。
- 真实 SSE、刷新后的 History 和轨迹都能看见同一失败结果；不新增客户端协议。
- Provider 引用失效与搜索 API 异常同样走工具结果失败；Fetch 不被搜索配置阻断；取消仍有效。
- DeepSeek 直调 Messages，Flash/Pro 用结构化结果验收；测试不得用只有文本或伪工具标签的响应通过。
- Kimi 请求中无 model、不调用 Chat；旧 model 读取忽略、下次保存移除，Key/Provider 引用正确保留。
- 第三方保持既有适配；默认值不引发自动选择、自动联网或供应商切换。
- 管理接口、四个选项、凭据切换/掩码/测试/保存及备份恢复与旧配置兼容。
- 搜索不改变主会话模型协议；普通工具、MCP、旧原生搜索事件回读保持可用。
- Fetch/Image 原有 SSRF、重定向、大小、超时、Markdown 图片与历史安全引用回归按影响范围执行。

研究依据与接口实测见 [横向研究](web-tools-research.md)。

## 2026-09-19 本地验证记录

通过正式管理草稿测试 `POST /api/v1/global-settings/web-search/test`，沿用本地已配置的 Provider Key，
未保存测试草稿、未覆盖原搜索配置：

| 服务 | 本次结果 | 本次耗时 |
| --- | --- | --- |
| Kimi Search API（无模型） | 成功，验证真实搜索结果 | 1510 ms |
| DeepSeek `deepseek-flash` Messages | 成功，验证结构化搜索结果 | 3894 ms |
| DeepSeek `deepseek-v4-pro` Messages | 成功，验证结构化搜索结果 | 15388 ms |
| DuckDuckGo | 成功，验证实际搜索结果 | 约 11360 ms |

这些为单次观测，不是持续可用性或性能承诺。Tavily、Exa、You.com 本轮仅做既有适配回归，未做真实 Key 联调。
本轮 17 个模块共 164 项定向回归通过，覆盖配置/API/初始化、模型装配、Provider、旧原生历史读取、
Fetch/图片/SSRF，以及新增 3 项 SQLite 集成：无配置、旧 native、失效引用均产生
`tool_call → failed tool_result → final`，并核验事件持久化及 History 投影。
Console 14 项测试和桌面、640 px 窄屏布局验证通过；本地 API 8000 与网关 8010 已启动。
上述是本次改动范围的验证，不代表全仓全部测试或所有供应商实测通过。
