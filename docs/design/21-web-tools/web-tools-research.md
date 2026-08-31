# Web 工具横向研究

## 结论

行业实现并不是“让代码环境直接联网”这一条路线，而是把搜索、网页抓取、业务 HTTP 和代码沙箱
分开授权。模型看到稳定的 `web_search(query)` / `web_fetch(url)`，宿主在后面选择 Provider、
限制网络目标并记录审计；Research 是调用这些工具的工作流，不是另一家搜索引擎。

## 横向实现

| 项目 | 搜索 | 抓取 | 代码网络边界 | 对 DeepAgent 的启示 |
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

## 上游能力与本仓采用

| 能力 | 上游已有 | DeepAgent采用方式 |
| --- | --- | --- |
| Provider原生搜索 | Pydantic AI `WebSearch` | 当前模型模式，失败不降级 |
| DuckDuckGo | Pydantic AI Common Tool | 固定Provider模式 |
| Tavily | Pydantic AI Common Tool | 固定Provider模式 |
| Exa | Harness `ExaSearch` | 固定Provider模式，只导出搜索 |
| You.com | Harness `YouSearch` | 固定Provider模式，只导出搜索 |
| SSRF-safe Fetch | Pydantic AI `WebFetch` | 5 MiB/50K/30秒、禁止私网与凭据 |
| 工具返回视觉内容 | Pydantic AI `ImageUrl`/`BinaryContent` | v0.6.6新增`web_image_view`返回小型URL引用；直接Fetch二进制保持原行为 |
| DeepSeek搜索 | DeepSeek Responses `web_search` | 薄适配层，默认`deepseek-v4-flash` |
| Kimi搜索 | Kimi `$web_search` | 薄适配层，默认`kimi-k3` |

[DeepSeek Responses API](https://api-docs.deepseek.com/api/create-response/)把`web_search`作为服务端
工具；[Kimi官方说明](https://platform.kimi.com/docs/guide/use-web-search)使用
`builtin_function/$web_search`并要求回传工具参数完成循环。两者共享“同一把Key可用于聊天和搜索”
这一凭据事实，但协议、模型要求和响应形态不同，因此在固定Provider模式下仍由不同适配层处理。

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
