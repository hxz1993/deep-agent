# 管理员用量统计

Console「会话 → 用量 → 日志」中的用量页仅供管理员访问。它读取仍保留在 `chat_session_events` 中的真实模型响应，不是费用账单，也不实施额度限制。删除会话会同时删除可统计的历史用量。网络搜索及图片生成的独立 API 消费不纳入本页。

## 口径

- 一次 Pydantic AI `ModelResponse` 计一次模型请求；同一响应拆成多条轨迹事件，只在一条事件的 `usage.calls[]` 中承载，查询再按会话和请求标识去重。
- `input_tokens`、`output_tokens` 使用单请求 `RequestUsage`；总量为两者相加。缓存读取／写入包含在各自的 SDK Usage 中，不额外叠加总量；`RunUsage` 累计量不能当成单次消费。
- `purpose` 分为 `chat`、`title`、`compaction`。标题失败或被用户改名时，已取得的模型用量仍保存在原有 fallback 标题事件；压缩失败或取消不抹去已有摘要调用。
- 用户归属取会话的平台用户 ID；渠道会话不按企微发言人重归属。日期取承载事件的 `created_at`，按查询指定时区分组，时间筛选为左闭右开。
- 字段未报告记为 `null`，明确报告零记为 `0`；部分数据仅汇总已知值并提示缺口。旧历史未回填或不能验证的响应不伪造用量。

## 数据结构和迁移

Alembic `007` 仅在 `chat_session_events` 增加可空 `usage_model_id` 和 `usage`（兼容数据库的 TextJSON），以及 `created_at, usage_model_id` 索引。`usage` 为 `{"version":1,"calls":[...]}`；每个调用包含 `request_id`、`kind`、`purpose`、模型／Provider ID 与名称快照、输入／输出／缓存字段及完整性状态。模型 ID 不加外键，删除配置后仍可从历史快照展示。

历史回填独立执行，不在启动时扫描：

```bash
PYTHONPATH=src .venv/bin/python -m maintenance.usage_backfill --config ~/.config/deep-agent/sqlite.env
PYTHONPATH=src .venv/bin/python -m maintenance.usage_backfill --config ~/.config/deep-agent/sqlite.env --apply
```

第一条只预检，第二条只回填可验证的旧聊天响应；标题与压缩无法拆分的历史不猜算。迁移降级时若存在非空 `usage` 会拒绝，以免静默丢失数据。

## 接口

均要求 Console 管理员，路径前缀 `/api/v1/diagnostics/usage`。公共筛选参数为 `start`、`end`（UTC ISO 时间，最多 31 天）、`timezone`（IANA 时区）、可选 `agent_id`。

| 接口 | 作用 |
| --- | --- |
| `GET /` | 指标、按日趋势、分组序列、用途分布、用户 Top 5 及不完整数 |
| `GET /details` | `group_by=model/user/agent/date`，支持 `sort`、`descending`、`limit`、`offset`；先全量聚合再分页 |
| `GET /sessions` | `group_by`、`key`、`offset`；查看分组贡献会话并跳转现有管理员详情 |
| `GET /export` | 同明细筛选与排序，`scope=all/page`；仅导出汇总 CSV，无正文、Payload 或凭据 |

页面默认最近 7 天，时间下拉与会话页共用最近 1／7／30 天、自定义时间及浮层组件；切换时间或 Agent 自动查询，查询按钮用于刷新。趋势默认无圆点折线，可切换堆叠柱状图；指标只选总 Token／请求，分组只选模型／Agent／用户。用途保留底部小卡片，并补充日均 Token、单次平均 Token、峰值日期摘要。右侧明细默认按模型，支持搜索、排序和每页 10 条；点击行可看贡献会话。所有汇总从相同事件事实计算，不另建 Usage 表或 Redis 计数器。

### Console 布局调整（2026-09-23）

上半区左侧为趋势与纵向图例，右侧集中展示指标、用途分布、用户 Top 5 和平均值。下半区采用左侧汇总表、右侧贡献会话的联动布局，选中行不替换汇总表。趋势指标按钮显示 Token / 请求。1250px 以下概览上下排列，800px 以下明细与贡献会话也上下排列，保持正常字号。

本次调整：趋势为前四加其他，分组顺序 Agent / 模型 / 用户；明细不再提供用途维度。顶部独立卡展示累计、日均、单次平均 Token 和请求数；用途、输入输出、峰值合为六项统计。贡献会话显示当前范围内最后一次用量事件时间，使用加载更多取代分页。

汇总工作台默认按 Agent 查看，模型、用户并列，日期放在更多入口；初次加载和切换视角自动选中首项并展示贡献会话。不足一页只展示总数提示。顶部第四卡改为峰值日期，模型请求数移入六项统计；图表类型使用单图标切换。

明细搜索入口及 API 查询参数已移除。汇总表新增单次平均 Token 和用量占比；贡献会话仅显示模型请求数，不再查询或展示提问数。
