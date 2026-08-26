# 上下文压缩设计（v0.6.0）

本文是当前实现口径。压缩没有独立数据表；一次压缩就是`chat_session_events`中的一条
`event_type=compaction`生命周期事件。

## 事实源与模型可见历史

原始完整Message（含Tool Call/Result、Context Reference和Plan结果）永久保留为Event。最新有效模型历史为：

```text
最新 completed Compaction 渲染出的结构化摘要锚点
+ source.through_sequence 之后的完整消息事件
```

失败、超时、取消、Stale或Interrupted事件只用于审计，不改变模型历史；每次成功压缩的Cutoff只能
向前移动。Provider原生`CompactionPart`按普通消息Part持久化，只能回传同一Provider，不能替代
DeepAgent的跨Provider摘要事件。Evidence使用`source_event_id + part_offset + call_id`精确回查
完整Message内的原始Part。

## 触发、边界与预算

- 自动压力阈值：有效输入预算的80%；目标压回55%。
- 手动压缩与明确Provider Context Overflow复用同一算法。
- 只选择最旧的安全完整前缀；不切断Message、Tool Call/Result或Deferred Pending；当前活跃Turn保留。
- 摘要固定使用当前 Agent 的有效模型，不配置第二套模型映射。
- 摘要模型窗口放不下候选前缀时失败关闭，不递归生成多层摘要。
- 新锚点加保留尾部必须小于原前缀并达到目标Token，否则不提交成功。

环境配置：

```dotenv
DEEP_AGENT_CONTEXT_COMPACTION_TRIGGER_RATIO=0.80
DEEP_AGENT_CONTEXT_COMPACTION_TARGET_RATIO=0.55
DEEP_AGENT_CONTEXT_SUMMARY_TIMEOUT=300
DEEP_AGENT_CONTEXT_TOOL_PRUNE_THRESHOLD=8192
DEEP_AGENT_CONTEXT_TOOL_PRUNE_HEAD=4096
DEEP_AGENT_CONTEXT_TOOL_PRUNE_TAIL=1024
```

裁剪三项必须为正数且`head + tail < threshold`。

## 摘要与确定性证据

摘要固定八个英文标题，正文沿用会话主要语言：

1. `Primary Request and Intent`
2. `Key Technical Concepts`
3. `Files and Code`
4. `Errors and Fixes`
5. `Pending Jobs`
6. `Current Work`
7. `Next Step`
8. `Critical Context`

历史内容只作为待总结数据，不执行其中指令；空章节写“无”。提示词要求保留用户纠正、有效约束、
路径、命令、错误、ID、Call ID、数值和当前状态，并排除Token、Cookie、API Key与临时签名URL。

Tool Evidence和Context Evidence不由模型生成：程序从真实Event确定性提取Call/Result来源、参数/结果
Hash、完整性、Artifact以及`id/name/code/path/alias/key/type/status/total/count`等稳定字段。区域选择、
元素信息和对象/成员ID保留来源Event、Payload Hash和安全稳定字段；小型安全Payload可直接进入锚点。
这些索引只帮助模型知道“查过什么、证据在哪里”，不复制全部结果，也不提供透明业务缓存。

## 超大工具结果

新结果优先使用Harness`ToolOutputLimits + Spill`保存到平台File；模型History保存预览、Artifact ID、
原始大小和Hash。Spill失败时Event保留`inline_fallback`和`spill_failed=true`，模型只接收受限投影。

对摘要输入中仍然内联且超过8192个Unicode码点的单条Tool Result，只裁剪交给摘要模型的副本：

```text
头4096码点

[... tool result middle pruned ...]

尾1024码点
```

Call ID、Tool Name、错误、Outcome、Metadata和非文本Block保持原顺序；原Event/Artifact完全不改。
尾部保留区不偷偷裁剪，若其本身无法满足目标，返回`oversized_tail_result`。

## 生命周期、并发与SSE

开始时在真实触发位置插入一条`status=running`的Compaction Event；摘要请求期间不持有数据库事务。
成功提交前重新校验来源Event Hash与边界，再条件更新同一Event ID为终态。进程启动时遗留的Running
事件转为Interrupted。

摘要总预算默认300秒；外层SSE每约10秒发送非持久化`context_compaction_progress`，Phase只取
`preparing/summarizing/validating/committing/retry_wait`，不伪造百分比。客户端断开或用户取消会取消
内部摘要。Timeout不重试；只有30秒内快速返回的429/502/503/504，在剩余预算至少60秒时最多重试一次。
网关仍需关闭SSE缓冲并把Read Timeout设为至少330秒，建议360秒；该部署项不由本仓应用代码修改。

明确Provider Context Overflow只在首个尝试尚未输出正文、工具调用或其他模型事件时，压缩后安全重试
一次；已发生可见输出或潜在副作用时返回原Overflow，避免重复执行工具。

## 校验与回查

提交成功必须同时满足：八段标题完整有序、内容非空、Finish Reason未截断、Evidence引用合法、工具
边界闭合、没有未知Part、结果确实变小并达到目标、来源Hash未变化。

模型可用`search_conversation_history`检索当前或本人同Agent会话，再用`read_conversation_event`按
Event ID或当前会话Call ID精读；Artifact中间内容继续用`read_tool_result(pattern=...)`读取。所有读取
先校验`agent_id + user_id`，App/Space只作来源过滤，不参与当前授权。
