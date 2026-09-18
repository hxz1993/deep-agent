# 21 · Web 工具

> 2026-09-19：**独立搜索服务改版v0.10.14 已实现**。

- [改版设计](web-tools-design.md)：统一普通 `web_search`、三个配置入口、旧 native 空态兼容、
  DeepSeek Messages / Kimi Search API，以及失败 Tool Result 后会话继续的事件契约。
- [研究与实测依据](web-tools-research.md)：DSH 源码链路、DeepSeek 不同协议实测、Pydantic AI/Harness 职责。
- [研发台账](../TODO.md)：本课题未完成项统一在此跟踪。

当前本地实现：取消会话模型原生搜索模式；控制台提供 DeepSeek、Kimi、网络搜索服务商三段切换及“暂不配置”文字选项，底层继续保存
具体 Provider。DeepSeek 异步直调 Messages，Kimi 直接检索、不选模型。空配置与旧 native 都显示未配置，
实际搜索调用产生可回读的失败结果，不中断主会话。

已移除默认 native、DeepSeek 搜索 Responses 和 Kimi 辅助模型循环；主会话模型协议不变。
已有 Provider 凭据的管理草稿测试通过：Kimi、DeepSeek Flash 与 Pro；未保存或覆盖现有搜索配置。
失败搜索的 Call / Result / Final、SQLite 事件持久化与 History 投影已定向验证。DuckDuckGo 真实草稿测试通过，Tavily/Exa/You.com 真实 Key 联调仍待补。
`web_fetch`、按需 `web_image_view`、SSRF 安全与既有聊天协议保持原有边界。
Internal HTTP、任意 HTTP Request、Python/Monty 任意联网不在本课题范围。
