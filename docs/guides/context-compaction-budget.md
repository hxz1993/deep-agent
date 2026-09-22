# 上下文压缩预算与失败诊断

## 占用与消耗（v0.10.18）

累计消耗是每次模型调用的Provider Usage之和；当前上下文占用是下一次请求的预计输入规模。
两者不能互换，也不能使用累计消耗计算窗口剩余。原始Usage随响应保留，展示Part拆分不重复累计。

统一估算器复用Harness的`estimate_context_tokens`，只在模型、历史、请求参数及当前指令指纹均
匹配时，使用最近响应的`input_tokens + output_tokens`，再加未测量的新增内容。中文保守计数、
已识别模型的tokenizer和新增图片补充继续生效；有效基准已覆盖的指令、工具Schema和媒体不重算。
压缩、模型切换、指令／工具变化及旧记录缺少指纹时回退完整估算，下一次正常响应建立新基准。
摘要请求的Usage单独记录，不进入主会话基准。

`GET /api/v1/sessions/{session_id}/context-status`及管理员详情中的同类上下文状态增加以下字段；
实时SSE使用`final.context.budget`携带相同计数语义：

| 字段 | 含义 |
| --- | --- |
| `estimated_request_tokens` | 当前可计算范围的请求估算，须结合`estimate_scope`理解 |
| `context_occupancy_tokens` | 当前上下文预计占用；不代表累计消耗 |
| `occupancy_source` | `provider_usage_plus_estimate`为有效Usage基准加增量；`estimated`为完整估算 |
| `usage_anchor_id` | 本次使用的基准标识；未使用基准时为`null` |
| `estimation_fallback_reason` | 回退原因；使用有效基准时为`null` |

常见回退原因包括`usage_unavailable`、`anchor_unverified`、`model_changed`、`history_changed`、
`request_parameters_unavailable`、`request_parameters_changed`和`instructions_changed`。
`estimated_history_tokens`仍是历史消息估算，不改成完整请求占用。

实时请求有最终工具和指令参数时使用`full_model_request`；历史重开的只读状态缺少这些参数，使用
`history_only`，不承诺恢复实时完整请求数值。有持久化基准且其他条件匹配时，此处回退原因为
`request_parameters_unavailable`；真正的新请求组装完成后仍可验证并复用基准。
客户端应区分这些范围与来源，缺失数据显示“—”，不要伪装成Provider实际值或零消耗。

模型窗口与项目有效配置保持一致。SDK的`context_window_used`只用于理解最近响应时点的占用比例；
输出预留、安全余量、80%触发／55%目标和超窗兜底继续使用原有规则。主动和自动压缩共用估算及
压缩执行链路，只在触发条件和调用场景上有差异。

## 预算边界与失败处理

压缩成功的目标作用于整个模型请求，不只作用于摘要正文。选择安全历史边界前，需要为请求固定开销、保留尾部、确定性 Evidence、摘要输出及估算余量留预算。

- Evidence 不属于摘要模型的输出上限；遗漏这一项会导致模型生成有效摘要后仍然 `target_not_met`。
- 准备期先计算最大可压缩安全前缀的 Evidence，再选择较小前缀并复核。Evidence 排序导致体积变化时，可退到已测量的最大安全前缀；不逐个重建全部历史，不追加摘要模型重试，也不删除当前受保护轮次。
- 持久化 Evidence 含原 Event 定位信息，可能与运行时索引大小不同。事务内必须对最终可回读的 Anchor 再次执行同一预算校验，通过后才能完成 Compaction Event。
- `target_not_met` 和 `persisted_target_not_met` 是失败，不是 `noop/no_reduction`。失败保留原消息和上一有效 Anchor。
- SSE 中 `after_tokens` 表示实际生效历史的估算；失败时可仍等于 `before_tokens`。`candidate_tokens` 才是被拒绝候选的完整估算，不能把二者相等解释成模型没有压缩。
- 失败 Event 的 `budget` 保存 `target_tokens/candidate_tokens/retained_tail_tokens/evidence_tokens/summary_tokens` 等数值，并保留安全的 `summary_call` 使用量，不保存被拒绝摘要正文。

手动压缩流必须显式关闭内层生成器，取消并等待摘要任务退出，以同一个 Compaction Event 收口为 `cancelled`。普通聊天主动停止不追加回执或异常；有部分正文时尽量保留原生中断消息一次。执行异常终止使用 `execution/error`，不作为模型正文。取消清理需避开传输层重复取消，但数据库写入仍受超时约束。

这些保证不覆盖进程被强杀、数据库完全不可写等情况。线上仅有 User Event 不能单凭形状确定断流原因，应结合对应 Run 的服务端日志和 Pod 生命周期。
