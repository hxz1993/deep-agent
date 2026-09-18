# Web 工具横向研究

> 2026-09-19 更新：区分历史接口探测与当前实现验证。独立搜索改版纳入 v0.10.14；实现边界以 [设计](web-tools-design.md) 为准。

## 结论

行业实现并不是“让代码环境直接联网”这一条路线，而是把搜索、网页抓取、业务 HTTP 和代码沙箱
分开授权。模型看到稳定的 `web_search(query)` / `web_fetch(url)`，宿主在后面选择 Provider、
限制网络目标并记录审计；Research 是调用这些工具的工作流，不是另一家搜索引擎。

## 横向实现

| **项目** | **搜索** | **抓取** | **代码网络边界** | **对 DeepAgent 的启示** |
| --- | --- | --- | --- | --- |
| OpenAI Codex | 模型侧 Web Search 独立开关 | 由产品工具提供 | 命令联网受独立 Sandbox Network Permission 控制 | Web 工具与命令网络不是一个权限 |
| DeepSeek Harness | 模型侧固定 `web_search`，Provider 位于 `ctx.web` seam 后 | 独立 `web_fetch` | Code工具只能编排显式注入函数 | 稳定Schema、可替换后端、统一展示 |
| LangChain Deep Agents Code | 当前搜索主要走 Tavily | 独立 `fetch_url`，含SSRF防护 | 框架工具与代码后端分离 | Search和Fetch不能合成任意HTTP |
| Dify | 搜索由插件/Provider提供 | HTTP Request/远程获取走统一策略 | 通用HTTP经SSRF Proxy | 内网与任意Header不能直接给模型 |
| Pydantic AI 2.35.1 | `WebSearch`支持Provider原生或本地策略；DuckDuckGo/Tavily有现成Tool | `WebFetch`有本地SSRF-safe实现 | 不自动给任意Python/Monty Socket | Core能力可直接作为运行底座 |
| Pydantic AI Harness 0.26.0 | 提供 Exa、You.com适配 | Research示例组合Core WebFetch | Research Agent只是能力组合 | Exa/You复用Harness，Research无需新工具类型 |

核验入口：

- [Codex命令网络权限](https://github.com/openai/codex/blob/main/codex-rs/prompts/templates/permissions/approval_policy/on_request_rule_request_permission.md)
- [DeepSeek Harness Web工具](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/web/tool-web/README.md)
- [DeepSeek Harness CLI组合](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/composition.md)
- [Deep Agents可插拔搜索讨论与当前Tavily实现](https://github.com/langchain-ai/deepagents/issues/3979)
- [Dify SSRF客户端](https://github.com/langgenius/dify/blob/main/api/core/helper/ssrf_proxy.py)
- [Pydantic AI Harness Research Agent示例](https://github.com/pydantic/pydantic-ai-harness/blob/main/examples/research_agent.py)

## 上游能力与本次采用边界

| **能力** | **上游已有** | **本次决定** |
| --- | --- | --- |
| 主会话服务端原生搜索 | Pydantic AI `WebSearch` / `WebSearchTool`，由模型适配器映射协议 | 本次移除产品模式，未配置不再默认走这条路径；不否定供应商能力 |
| DuckDuckGo / Tavily | Pydantic AI Common Tools | 沿用独立搜索工具适配 |
| Exa / You.com | Harness `ExaSearch` / `YouSearch` | 沿用独立搜索工具适配 |
| DeepSeek 独立搜索 | DSH 以普通工具包装 Anthropic Messages 的服务端搜索 | 改为异步直调 Messages，默认 Flash，不创建辅助 Agent |
| Kimi 独立搜索 | 官方 `/v1/tools/search` | 直接检索，不选择模型，不做辅助模型总结 |
| SSRF-safe Fetch | Pydantic AI `WebFetch` 与安全下载器 | 保留现有安全适配 |
| 工具返回视觉内容 | Pydantic AI `ImageUrl` / `BinaryContent` | 保留 `web_image_view` URL 引用和直接 Fetch 二进制行为 |

### DeepSeek Harness 源码结论

2026-09-18 核验本地 Harness `c291e7961`，并对照官方 master 的搜索适配源码：

1. 外层模型得到普通 `web_search(queries)` 函数定义，DSH 经 `ctx.web.search()` 执行。
2. 默认 DeepSeek provider 独立调用 `https://api.deepseek.com/anthropic/v1/messages`，默认模型
   `deepseek-v4-flash`，工具为 `web_search_20250305`，同一 DeepSeek Key 可复用。
3. 请求只包含搜索指令与 query，不包含主会话完整上下文。搜索发生在该独立模型请求内部。
4. 适配器从 `web_search_tool_result` 提取来源，并从 citations 合并引用片段；不把辅助模型生成的
   完整回答直接返回主模型。没有结构化搜索结果块则失败。

所以“外层是普通工具”和“内层使用原生服务端搜索”同时成立。不能因为 DSH 中 Flash 能搜，就推定
所有 DeepSeek 协议都支持同样参数；也不能因为某协议未返回搜索证据，就认定整个型号不具备搜索能力。

核验源码：

- [模型侧工具](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/web/tool-web/src/search.ts)
- [DeepSeek 搜索请求与结果转换](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/web/web-search-deepseek/src/provider.ts)
- [独立搜索配置与凭据解析](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/web/web-search-deepseek/src/index.ts)

### 接口实测证据与限制

以下是 2026-09-18 本地使用已配置凭据直连官方 API 的一次验证记录，不是持续可用性或延迟承诺：

| **请求路径** | **模型** | **观察结果** |
| --- | --- | --- |
| Anthropic Messages + `web_search_20250305` | `deepseek-flash` | HTTP 200，1 个结构化搜索结果块、10 条来源，约 4.9 秒 |
| 同上 | `deepseek-v4-flash` | HTTP 200，1 个搜索结果块、10 条来源，约 5.8 秒 |
| 同上 | `deepseek-v4-pro` | HTTP 200，3 个搜索结果块、30 条未去重结果，约 17.1 秒 |
| Responses + `web_search` | Flash | 已测请求没有真实搜索记录，部分响应只生成伪工具标签文本 |
| Responses + `web_search` | V4 Pro | 已测请求返回真实搜索调用记录 |

这些证据只确认所测协议和请求执行了搜索，不证明内容准确、每轮必搜或主会话流式/MCP/多轮历史完全兼容。
不按测试结果生成模型黑名单。Pydantic AI 支持 `AnthropicModel`、自定义基址的 `AnthropicProvider`
与原生搜索，但本次不迁移 DeepSeek 主会话协议。

Kimi 当前采用的新接口为官方 `POST /v1/tools/search`，参数是 `text_query`、`limit`、
`timeout_seconds`、`include_content`，没有 model。此前辅助 Chat 模型 + Search API 链路已实测，
本地实现已删掉辅助模型往返，只保留检索。早期 Formula、`$web_search` 与 Responses 请求的行为差异
不能概括成“Kimi 永久不支持原生搜索”；本次无需依赖这些路径。

接口与框架参考：

- [Kimi Search API](https://platform.kimi.com/docs/api/tools-search)
- [Pydantic AI WebSearch](https://github.com/pydantic/pydantic-ai/blob/main/docs/capabilities/web-search.md)
- [Pydantic AI Anthropic 接入](https://github.com/pydantic/pydantic-ai/blob/main/docs/models/anthropic.md)
- [DeepSeek Responses 说明](https://api-docs.deepseek.com/zh-cn/guides/responses_api/)

### 改版后的管理接口实测（2026-09-19）

通过 DeepAgent 正式 `POST /api/v1/global-settings/web-search/test`，草稿指定已有 Provider 引用，
运行时解析其 Key，不复制或显示凭据，也不保存草稿。Kimi 无模型 Search API 成功，耗时 1510 ms；
DeepSeek Messages 的 `deepseek-flash` 成功，耗时 3894 ms，`deepseek-v4-pro` 成功，耗时 15388 ms。
测试沿用生产搜索执行器，成功以结构化搜索结果为依据，不再调用辅助 Agent 或 Kimi Chat 总结。
DuckDuckGo 同一草稿测试路径也返回真实结果，约 11.36 秒。这些是一次验证的耗时，不代表固定延迟；
Tavily、Exa、You.com 的真实 Key 联调本轮未执行。

### 采用原则

统一外层工具契约不意味着所有后端实现相同。直接检索服务不再加模型总结；DeepSeek 暂通过 Messages
模型请求获取服务端搜索结果。延迟比较须区分网络、检索和模型输出，不能只凭 Agent 对象数量判断性能。
未配置也应留下真实工具调用与失败结果，让模型继续回答，不能在 Agent 初始化阶段终止整轮会话。

## Research 的位置

Researcher不是第3个网络工具，也不是Exa/Tavily的别名。它通常是：拆分问题 → 多次Search →
按URL Fetch → 交叉验证 → 带来源综合。Pydantic AI/Harness用Agent、WebSearch、WebFetch、
ToolOutputLimits和可选子任务组合表达；v0.6.5先提供Search/Fetch，v0.6.6补充按需视觉读取。
后续仍可在不改变数据库或Provider配置的前提下增加Research Skill/Agent模板。

## 图片结论

Pydantic AI原生WebFetch考虑了“直接URL就是图片”的场景，返回`BinaryContent`；这适合必须由宿主
下载的媒体，但字节会成为工具结果，可能触发大工具治理。它的HTML转换默认移除`img`，因此模型
既不知道图片位置，也不能自主选图。

v0.6.6不自动下载整页图片，也不把图片Base64塞入历史。Fetch只保留原位置Markdown引用；
`web_image_view`返回原生`ImageUrl`，Pydantic AI在同一个Agent Loop的下一次Provider请求中把它映射
为URL视觉参数，或在`force_download=true`时临时下载后映射。这个中间对象可序列化为URL/MIME/模式，
不是Code Mode变量，也不要求附件上传。AI Labs中常见的浏览器截图、页面渲染或视觉附件属于更重的
浏览器/文件链路；当前Web课题只提供公开静态图片的最小、安全读取路径。
