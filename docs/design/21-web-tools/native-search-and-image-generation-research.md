# 原生搜索与原生生图

2026-11-19 归档。两项能力处于研究阶段。讨论原文见 [DeepAgent 原生联网搜索与图片生成研究](https://chatgpt.com/space/page_eea30f9bcd488191a5bd7b510f8b5267)，展示见 [局部交互原型](Prototype%20%5BDeepAgent%20%E5%8E%9F%E7%94%9F%E7%BD%91%E7%BB%9C%E6%90%9C%E7%B4%A2%5D/index.html)。

## 搜索接口实测

用本机配置的对应供应商凭据直连官方 API，按响应事件和字段核验。测试问题使用公开网页，未改变运行配置。

| 供应商、模型与协议 | 声明 | 本次响应 |
| --- | --- | --- |
| DeepSeek Flash · Chat Completions | `deepseek_web_search` | HTTP 200，`message.server_tool_calls` 含已完成的搜索记录，3 个查询、15 条来源、4 条正文引用 |
| DeepSeek Flash · Responses | `deepseek_web_search` | HTTP 200，`web_search_call` 已完成，2 个查询、12 条来源、2 条正文引用 |
| DeepSeek Flash · Chat Completions / Responses | `deepseek_read_url` | 两条路径均 HTTP 200，响应含已完成的 `read_link` 和页面元数据，用量各记 1 次抓取，本次无正文引用 |
| OpenAI `gpt-6.2-astra` · Responses | `web_search` | HTTP 200，`web_search_call` 已完成，含查询字段。本次未要求完整来源列表，响应中未取得来源列表或正文引用 |

同一工具是否提供查询、来源和正文引用，取决于供应商、协议和具体响应。先以实际执行事件为判断依据，再考虑哪些详情适合展示。上一轮还验证了 DeepSeek V4 Pro，Flash 和 Pro 都能用 `deepseek_web_search`。OpenAI 风格的 `web_search` 在 DeepSeek Responses 中只实测 Pro 执行，Flash 返回文本。DeepSeek Flash 关闭思考并同传专用原生搜索与普通函数时，三种协议均返回 400。开启思考后，三种协议均实测完成搜索并发起业务函数，回传函数结果后可续跑。参见 [DeepSeek Web Tools](https://api-docs.deepseek.com/guides/web_tools/)、[DeepSeek Responses](https://api-docs.deepseek.com/guides/responses_api/) 和 [OpenAI Web Search](https://developers.openai.com/api/docs/guides/tools-web-search)。

## 当前逻辑与原型

- 现有 `web_search`、`web_fetch` 由 DeepAgent 作为普通工具执行，并将结果回传主模型。原生能力则随主模型请求声明，由供应商在该请求内执行，DeepAgent 消费响应中的实际事件。
- 轨迹原型把「已联网」放在助手行及其右侧详情，计数只统计独立工具。测试页对话不重复插入联网条目，正文按实际返回展示引用。独立搜索继续按普通工具展示。跨供应商、多种工具的完整展示规则待验证后确定。
- 供应商弹框的「原生能力」为只读示例，按现行协议显示可用模型。用户可用的能力需结合供应商实例、实际端点、协议和模型判断。通用工具标签和图片模态只提供目录信息。能力来源与是否持久化留待评审。
- Pydantic AI 2.70.0 的默认 DeepSeek Chat 不发送 `deepseek_web_search`、`deepseek_read_url`。如后续接入，可保留官方 Provider，扩展模型请求序列化、流式事件和历史回传。`WebSearch` 和 `WebFetch` 的 `local=` 可复用现有工具，选择发生在请求组装时。选择独立搜索需要先满足相同的授权和配置条件。[Pydantic AI WebSearch](https://pydantic.dev/docs/ai/capabilities/web-search/)。

## 待讨论 · 原生工具上下文怎么续接

- 区分本次请求内和下一次请求。供应商在同一次请求里可以使用刚搜到或读到的内容继续生成。用户下一轮能否引用这些内容，取决于端点是否保存会话状态以及客户端怎样续接。
- DeepSeek Responses 为无状态接口。官方 Web Tools 说明要求按原样传回相关输出项，保留供应商字段和签名，以恢复先前搜索结果。Chat Completions 路径对应回传助手消息里的 `server_tool_calls`。普通文本摘要能保留哪些信息、哪些任务需要原始结果，仍待实测。[DeepSeek Responses](https://api-docs.deepseek.com/guides/responses_api/) · [DeepSeek Web Tools 多轮会话](https://api-docs.deepseek.com/guides/web_tools/)。
- OpenAI Responses 提供 `previous_response_id` 和 Conversations 两种续接方式。需要验证当前配置下供应商实际保存和续接哪些工具上下文，以及关闭保存、切换供应商或发生本地压缩时，DeepAgent 应回传哪些历史。轨迹显示、供应商原始续接数据和本地摘要的保留范围分别讨论。[OpenAI 会话状态](https://developers.openai.com/api/docs/guides/conversation-state)。

## 生图研究边界

- 优先评估主会话原生生图。OpenAI Responses 可声明 `image_generation`，在同次响应中接收工具事件和图片结果。Images API 是应用直接请求生成或编辑的接口。Codex Harness 生图属于产品工具路径。API Key、订阅 OAuth 和中转端点分别验收，不把授权、余额或模型支持相互外推。[OpenAI 图片生成](https://developers.openai.com/api/docs/guides/image-generation)。
- 供应商生图成功与平台文件交付是两个步骤。待验证范围含图片格式、流式进度、FileStore 保存、下载授权和下一轮参考图续接。本轮未进行 OpenAI 生图真测，也未修改产品、存储或迁移。
