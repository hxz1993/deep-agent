# 18 · 会话事件与轨迹

> v0.6.0实施；Alembic head：008。

DeepAgent会话域只有`chat_sessions`和`chat_session_events`。Event Store是模型恢复、会话历史、
Console轨迹、Plan和持久化SSE的唯一事实源；不保存Provider HTTP Request/Response，也不建立第二份
模型消息、轨迹、Plan、Evidence或搜索表。

## 表与粒度

`chat_sessions`保存全局唯一`session_id`、非空`agent_id + user_id`、可空`app_id/space_id`、标题、
`next_sequence`和时间。当前权限只按Agent+User，App/Space仅记录首次创建来源，缺失时保持NULL。

`chat_session_events`以一个完整Pydantic AI公共Part一行为粒度：

- 定位列：`event_id/session_id/sequence/schema_version/event_type/status`。
- 生命周期：`turn_id/run_id`。
- Message边界：`message_id/part_index`。
- 工具配对：`call_id/tool_name`。
- 数据：脱敏确定性`search_text`、完整公共Part`payload`、发生和更新时间。

需要排序、Join、状态过滤和高频配对的字段才单列；Provider/模型/Usage/Finish Reason、消息级元信息、
工具参数和结果、Context Reference、Artifact、Compaction来源与Evidence均在Payload。每个Message只在首个
Part保存一次`message_meta`。未知Part原样存为`model/unknown_part`，UI可显示占位，但模型恢复与压缩
失败关闭，不能静默遗漏。

普通问答通常三行（Run、User、Assistant）；一次工具调用通常五行。Token Delta、心跳和Step Start/End
不入库。Tool Call和Result必须分行，因为它们可能跨Run和Deferred阶段出现。

## Turn、Run、Deferred与Plan

- 新用户问题创建新`turn_id`；每次Pydantic执行创建新`run_id`。
- UI Tool/HITL Pending后，原Run原位更新为Pending；提交Deferred Result创建新Run并继承Turn，
  Payload中的`resume_from_run_id`指向原Run，Call/Result继续按`call_id`配对。
- Run和Compaction插入Running后原位更新为终态；其他消息Event追加后不可修改。
- Planning只暴露`write_plan`。完整计划快照仍是普通Tool Call/Result；同Turn恢复最近成功快照，新Turn
  使用空PlanStore。Console会话级Plan显示最后成功快照，轨迹保留每次更新的原位置。

## 编解码与并发

写入时把`new_messages()`按公开Part拆成Event；读取时按Sequence、Message ID和Part Index重组，经
`ModelMessagesTypeAdapter`恢复为Pydantic`message_history`。Provider差异由Pydantic适配，不改变表结构。
Message ID由Session、Run和Message序号确定性生成；重复写入Payload Hash一致视为幂等，不一致报错。

Session行锁与`next_sequence`批量分配顺序；SQLite使用串行写事务，MySQL/PostgreSQL使用行锁。模型
请求期间不持有数据库事务。进程启动时遗留Running生命周期转为Interrupted。

## API与SSE

```http
GET /api/v1/sessions/{session_id}/events
  ?after_sequence=0&limit=100
  &event_type=&turn_id=&run_id=&call_id=
```

响应按Sequence分页，`limit`最大500，内部`search_text`不返回。旧的整包History与独立压缩字段不再是
新契约。`context-status`从最新成功Compaction、其后Event和当前TokenEstimator实时计算。

持久化SSE只有`session_event_appended`与`session_event_updated`，都携带完整Event，客户端按Event ID
Upsert；Text/Thinking Delta、临时工具状态和压缩心跳明确`persisted=false`。

Console普通聊天默认隐藏历史中已完成的压缩标记；直播时显示紧凑状态。轨迹按Sequence逐行展示原始
语义Event，Compaction终态更新原位置，不追加到列表底部。History、轨迹和Plan全部读取Event API。

## 搜索、安全与未来边界

每个Event最多保存64K NFKC归一化搜索文本，排除Reasoning和敏感字段。候选集先按Agent、User、会话、
类型与时间在SQL有界过滤，再由应用层进行中文连续子串、单字/二元组和英文/路径/ID评分。首版不做
向量检索或透明工具缓存。

Branch From本版不实现，也不预埋Parent列。稳定Event/Message/Call ID和完整边界为以后选择“复制前缀”
或“父链读取”提供基础，届时再按真实方案迁移。

交互原型仅保留作为视觉历史：

- [`Prototype DeepAgent会话轨迹.html`](Prototype%20DeepAgent%E4%BC%9A%E8%AF%9D%E8%BD%A8%E8%BF%B9.html)
- [`Prototype DeepAgent会话详情三方案.html`](Prototype%20DeepAgent%E4%BC%9A%E8%AF%9D%E8%AF%A6%E6%83%85%E4%B8%89%E6%96%B9%E6%A1%88.html)
