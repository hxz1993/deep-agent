# 会话、用户与群聊的图标和复制交互

2026-09-15 的三方案评审产物：[交互原型](index.html)、[三方案图片](three-options.png)。示例数据仅用于布局比较，原型不调用产品 API。

用户选择 A 后进一步收敛：保留首行类型图标；第二行默认显示“Session ID · 最新提问”，分隔点两侧留空，悬停或键盘聚焦时原位变成复制图标，不另留一个复制按钮空位。无悬停设备直接显示复制图标。该版本已用于会话列表和渠道用户／群聊列表；点击复制不打开会话。

图片与 HTML 保留最初三方案，不按它们覆盖最终交互。正式实现见[会话诊断指南](../../guides/diagnostic-sessions.md)、`console/diagnostics-sessions.js` 和 `console/diagnostics-channels.js`。
