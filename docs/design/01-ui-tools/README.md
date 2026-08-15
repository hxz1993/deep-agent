# 01 · UI 工具

> 首次研究：2026-06-23
>
> 当前状态：builtin/manual 两类来源已落地；MCP 自动发现方案已关闭

当前产品行为见：

- [`../../guides/ui-tools.md`](../../guides/ui-tools.md)：定义、Agent 选择和管理接口。
- [`../../guides/ui-tools-external.md`](../../guides/ui-tools-external.md)：Deferred/UI 工具接入协议。

设计演进见 [`ui-tools-history.md`](ui-tools-history.md)。该文档合并了早期扩展方案、
MCP 发现 RFC 和同步机制评审，只用于解释为什么最终没有采用 MCP 自动发现。

后续事项统一登记在 [`../TODO.md`](../TODO.md)。

当前页面业务工具、Harness `BrowserUse` 和 Provider Computer Use 的区别及旧
`feat/web-capabilities` 分支取舍，见
[`15 · 浏览器与页面操作`](../15-browser-and-page-actions/README.md)。
