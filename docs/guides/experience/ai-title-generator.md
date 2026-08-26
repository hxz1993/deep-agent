# 会话标题生成

DeepAgent 只在会话首轮自动生成一次标题。标题请求复用该轮实际生效的 Provider 与模型，包括请求级
`model_id` 覆盖；不存在标题专用模型、独立配置表或环境变量。

## 调用链

1. 持久化首条 `user/message`。
2. 根据正文、Context 标签和附件名称生成确定性兜底标题，并追加 `session/title` Event。
3. 正常 Agent 请求与独立标题请求并行执行；标题请求无工具、无历史、小输出预算、15 秒超时。
4. 标题成功时，仅当最新标题仍是对应兜底 Event，才更新 `chat_sessions.title` 并追加模型标题 Event。
5. SSE 在 `final` 前发送标题 Event。失败、超时或图片读取失败时保留兜底标题，不重试。

第二轮、Deferred 恢复和服务重启都不会再次自动请求标题。用户可手动改名；手动标题 Event 会阻止
尚未结束的模型标题覆盖。系统不支持清空、重置或重新自动生成标题。

## 安全输入

- 正文会规范化并限制长度。
- Context 只传类型、标签及 `id/name/code/path/alias/key/type/status/total/count` 等稳定小字段。
- 普通附件只传文件名和 MIME，不解析 DOCX、XLSX 或 PDF 正文。
- 图片最多读取首张，并通过当前用户的 `FileStore` 权限发送给模型；其他图片只传元数据。
- 不传 Context Metadata、附件提取正文、File URL、Provider Details、Header、Token 或凭据。

## Event 与客户端契约

标题使用现有 `chat_session_events`：

```json
{
  "event_type": "session/title",
  "payload": {
    "title": "分析预算差异",
    "source": "fallback | model | user",
    "source_event_id": "首条 user/message Event ID",
    "provider_id": "仅模型来源",
    "provider_type": "仅模型来源",
    "model_id": "仅模型来源",
    "model_name": "仅模型来源",
    "context_count": 1,
    "attachment_count": 2,
    "image_used": true
  }
}
```

`chat_sessions.title` 是列表投影，和 Event 在同一事务更新。标题 Event 可由 Event API 审计，并通过
`session_event_appended` SSE 推送，但不进入模型历史、压缩输入、搜索正文、普通聊天或轨迹。

客户端监听标题 Event 更新侧边栏和页头，不轮询 Session API。`PATCH /sessions/{id}` 只接受非空
`title`，用于手动改名；`auto` 字段与自动刷新入口已删除。

## 关键实现与测试

- `deep_agent/agent/title_generator.py`：安全投影、兜底和有界模型请求。
- `deep_agent/agent/runner.py`：首轮并行调度、SSE 顺序和失败闭合。
- `deep_agent/db/conversation.py`：标题 CAS、投影与 Event 原子写入。
- `tests/unit/test_title_generator.py`：输入安全、图片和失败行为。
- `tests/integration/test_session_title.py`：首轮一次性、SSE、回退和已删除接口。
- `tests/integration/test_session_rename.py`：手动改名及空标题拒绝。
