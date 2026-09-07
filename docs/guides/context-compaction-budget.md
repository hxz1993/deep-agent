# 上下文压缩预算与失败诊断

压缩成功的目标作用于整个模型请求，不只作用于摘要正文。选择安全历史边界前，需要为请求固定开销、保留尾部、确定性 Evidence、摘要输出及估算余量留预算。

- Evidence 不属于摘要模型的输出上限；遗漏这一项会导致模型生成有效摘要后仍然 `target_not_met`。
- 准备期先计算最大可压缩安全前缀的 Evidence，再选择较小前缀并复核。Evidence 排序导致体积变化时，可退到已测量的最大安全前缀；不逐个重建全部历史，不追加摘要模型重试，也不删除当前受保护轮次。
- 持久化 Evidence 含原 Event 定位信息，可能与运行时索引大小不同。事务内必须对最终可回读的 Anchor 再次执行同一预算校验，通过后才能完成 Compaction Event。
- `target_not_met` 和 `persisted_target_not_met` 是失败，不是 `noop/no_reduction`。失败保留原消息和上一有效 Anchor。
- SSE 中 `after_tokens` 表示实际生效历史的估算；失败时可仍等于 `before_tokens`。`candidate_tokens` 才是被拒绝候选的完整估算，不能把二者相等解释成模型没有压缩。
- 失败 Event 的 `budget` 保存 `target_tokens/candidate_tokens/retained_tail_tokens/evidence_tokens/summary_tokens` 等数值，并保留安全的 `summary_call` 使用量，不保存被拒绝摘要正文。

手动压缩流必须显式关闭内层生成器，取消并等待摘要任务退出，以同一个 Event 收口为 `cancelled`。普通聊天在模型首字前断流、没有可持久化 Assistant 时，仍必须保存独立 `run/terminal`；有部分正文时保留原生中断消息。取消清理需避开传输层重复取消，但数据库写入仍受超时约束。

这些保证不覆盖进程被强杀、数据库完全不可写等情况。线上仅有 User Event 不能单凭形状确定断流原因，应结合对应 Run 的服务端日志和 Pod 生命周期。
