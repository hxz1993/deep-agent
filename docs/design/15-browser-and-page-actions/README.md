# 15 · 浏览器与页面操作

> 首次研究：2026-08-11
>
> 当前状态：完成能力分层与旧分支审查；尚未实施 BrowserUse 或请求级页面能力

本课题区分三个经常被混称为“浏览器工具”的不同能力：

1. 操作用户当前打开的 DeepFOS 业务页面。
2. 让独立浏览器子 Agent 自主访问外部或未知网站。
3. 显式启用某个模型 Provider 的原生 Computer Use。

当前结论是三者不能互相替代。DeepFOS 页面操作继续基于现有 UI Tool、`ExternalToolset` 和
Deferred；Harness `BrowserUse` 只作为未来独立、默认关闭的外部浏览能力；远端早期
`feat/web-capabilities` 分支不整体合并。

完整审查、链路和采用边界见
[`browser-and-page-actions-design.md`](browser-and-page-actions-design.md)，实施状态见
[`../TODO.md`](../TODO.md)。
