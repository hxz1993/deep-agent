# 真实 Provider 与私有兼容端点验收

本次使用授权本地配置的 DeepSeek、Kimi、MiniMax，在隔离 SQLite 副本及 18014 验收 API 上执行。输入均为合成文本/时间查询/UI 确认，不调用客户业务写入，不修改线上 Agent。验收副本、原始响应及凭据不入 Git，收尾清理。

## 真实调用与独立回读

| Provider | 场景 | Session ID | 真实聊天请求数 | 输入 / 输出 Token |
| --- | --- | --- | ---: | ---: |
| DeepSeek deepseek-flash | HTTP 直接原生 JSON | fb10d01980834c0685f4f0cfe2a4063d | 1 | 3408 / 110 |
| DeepSeek | 查询时间 → UI Pending → 恢复输出 | 6a3c3d3cf05545a392ac7a41e66fd7de | 3 | 10590 / 329 |
| DeepSeek | 首次错误 const → 第二次修正 | a9c7192544b9411f851459beaa06cee3 | 2 | 5754 / 249 |
| Kimi kimi-k2.6 | HTTP 直接原生 JSON | e5303616b88a46c6951774170f72af2a | 1 | 2716 / 75 |
| Kimi | 查询时间 → UI Pending → 恢复输出 | c3c2218e41934b75bd2d57e47d5517c3 | 3 | 8653 / 352 |
| Kimi | 首次错误 const → 第二次修正 | f332eefb05064a41b1647b6a1858f2ed | 2 | 4523 / 125 |
| MiniMax MiniMax-M2.7 / Anthropic 协议 | 查询时间 → 原生 JSON | ea05875f50d14e3b8d4f77c9160213b0 | 2 | 5790 / 107 |

全部成功结果保持原生 JSON，Emoji/中文/扩展汉字完整落库。HTTP/SSE 的 output、History structured_output 与独立 SQLite payload 相等，item_id 等于 output_id。两家 Pending 期间修改当前 Agent 的编码和 Schema，恢复仍使用原快照；两次真实格式纠错均为 attempt=2。

独立只读 SQLite 按 request_id 去重 Usage calls，再按 provider_response_id 与真实 SDK response usage 核对，以上 7 会话的请求数、输入/输出 Token 均一致。每个会话只有一个 accepted 事实；output/attempt 的 usage 与 usage_model_id 均为 NULL，不重复计量。标题等辅助目的不算入表中聊天数。此处验证的是 Provider 报告的请求/Token，不冒充供应商账单金额对账；当前未取得实际账单，缺失费用仍为未知。

## 发现与修复

最初两家思考模式拒绝 tool_choice=required，真实接口返回 400。已按固定 SDK 2.46.0 的模型能力配置，对 DeepSeek flash/v4 与 Moonshot 思考模型使用允许的工具选择方式，不禁用思考、不退回文字、也不升级框架。服务端完整响应检查仍拒绝纯文字绕过，真实重测均通过。

## 私有兼容端点与拒绝行为

`tests/integration/test_structured_output_compatible_provider.py` 2 方法通过：实际 loopback TCP HTTP 服务承接真实 OpenAIChatModel SDK 请求，普通和 SSE 两路径返回同一原生 JSON 并写入真实 Event Store；线包没有 response_format，也不发送 strict=true。另返回 400 时，普通请求明确失败，流式返回 error，两条路径均没有已接受结果、没有换成纯文本重新请求。服务、线程、HTTP Client、EventPersister 和数据库 finally 清理。

此项验证私有兼容协议行为，不声称已遍历所有客户的私有网关或 Provider 型号。Provider 托管原生工具在远端已执行的部分不能回滚；宿主分发前检查只保证本机普通/MCP/UI 不先执行。
