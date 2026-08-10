# DeepAgent 附件解析与按需读取设计

> 状态：第一版已实现
>
> 数据库：沿用 v0.3.0 Alembic `001` 完整基线，不新增附件表

## 1. 背景与目标

旧链路在上传阶段尝试把 Office/PDF/文本抽成一个有长度上限的字符串，再随用户消息直接交给
模型。它有三个根本问题：

1. 大文件只保留开头，模型既不知道内容不完整，也无法继续读取。
2. 不论用户问题是否需要文件内容，整段文本都会消耗上下文。
3. 文档解析、模型原生模态和平台上传能力混成一套配置，难以描述真实能力。

本轮改成：

```text
平台上传文件
  → 会话只保存 file_id/name/mime 引用
  → Agent 明确启用 builtin:attachment 工具包
  → 模型按需调用对应读取工具
  → 工具返回安全大小的一页和继续读取游标
```

模型不需要预先猜 `offset` 和 `limit`。首次通常只给 `file_id`，发现 `has_more=true` 后才使用
`next_offset` 继续读。这与 Pi Agent、Hermes、OpenCode 等工具型 Agent 的渐进式文件读取方向
一致：工具提供合理默认页，不让模型在首次调用时承担文件规模判断。

## 2. 工具包与默认行为

能力目录新增：

```text
code: builtin:attachment
category: builtin
default_enabled: false
```

它和 MCP、Skill、UI 工具包一样由 Agent 的 `capabilities` 显式选择：

- `capabilities=null`：不启用附件工具。
- `capabilities=[]`：不启用附件工具。
- 包含 `builtin:attachment`：加载三个读取工具。
- Alembic `002` 将旧 `attachment` 编码一次性改为新包编码；运行时不保留旧编码。

Console 只把它显示为普通的“附件读取”工具包，不增加复杂的上传阻断流程。管理员负责给需要
读取文档的 Agent 勾选工具包。

## 3. 工具协议

### 3.1 通用响应

三个工具统一返回：

```json
{
  "file_id": "platform-file-id",
  "name": "report.docx",
  "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "unit": "block",
  "offset": 1,
  "limit": 100,
  "returned_count": 100,
  "total_count": 245,
  "content": "...",
  "has_more": true,
  "next_offset": 101,
  "truncated_by": "limit",
  "metadata": {}
}
```

规则：

- `offset` 从 1 开始。
- 不传 `offset` 时从第一项开始。
- 不传 `limit` 时使用工具默认页大小。
- 用户传入超过硬上限的 `limit` 会自动收紧。
- 单次工具文本输出最多约 50,000 字符。
- `has_more=true` 时使用 `next_offset` 读取下一页。

### 3.2 `read_text_attachment`

```text
read_text_attachment(file_id, offset=None, limit=None)
```

- 单位：物理行。
- 默认：500 行。
- 硬上限：2,000 行。
- 支持：TXT、CSV、JSON、Markdown、HTML、XML。
- 编码：UTF-16 BOM、UTF-8 BOM、UTF-8、GB18030，最后才使用替换字符兜底。
- 只有明确 JSON MIME 或 `.json` 文件名才格式化 JSON，不根据内容猜格式。

### 3.3 `read_docx`

```text
read_docx(file_id, offset=None, limit=None)
```

- 单位：逻辑块。
- 默认：100 块。
- 硬上限：500 块。
- 顺序：页眉 → 正文段落/表格 → 页脚 → 批注附录。
- 表格转成可读行，正文段落与表格保持文档内出现顺序。
- 内嵌图片最多返回安全数量；仅第一页且模型支持原生图片时作为文档级图片集合返回。
- 图片集合不承诺与正文的精确锚点关系。

第一版不承诺：

- 版面、分页、浮动对象和样式还原。
- 修订前后版本重建。
- 图片与具体段落的精确定位。
- 嵌入对象、宏和复杂域代码执行。

### 3.4 `read_xlsx`

```text
read_xlsx(file_id, sheet=None, offset=None, limit=None)
```

- 单位：物理行，包含表头。
- 默认：100 行。
- 硬上限：500 行。
- 未指定 `sheet` 时选择第一个可见工作表。
- 显式工作表名不区分大小写。
- 返回工作表列表、行列数、表头等元数据。
- 公式只读取文件中已有的缓存值，不在服务端计算。

## 4. 会话与权限边界

工具只接受当前 `(agent_id, session_id)` 范围内出现过的附件 ID：

```text
当前消息 attachments[]
  + 当前会话原始 chat_messages 中的 attachment-ref
  → 本轮 attachment_by_id 白名单
```

未知 `file_id` 在平台下载前直接拒绝。平台下载仍使用当前请求的认证 Header，因此同时具备：

1. DeepAgent 会话范围限制。
2. 平台当前用户文件权限限制。

Checkpoint 不能作为附件权限来源，因为它会裁剪旧消息；允许范围必须从完整原始历史建立。

## 5. 上传和文件安全

全局限制继续由环境变量控制：

```text
DEEP_AGENT_UPLOAD_MAX_SIZE_MB=20
DEEP_AGENT_UPLOAD_MAX_COUNT=10
DEEP_AGENT_UPLOAD_ALLOWED_MIMES=
```

默认每文件 20MB、每消息 10 个，大小与数量必须是正整数。Console 从
`GET /api/v1/uploads/capabilities` 获取最终 MIME、大小和数量限制并做快速提示，后端仍做最终
校验。

Office 文件在解析前检查：

- ZIP 成员数量、单成员大小和总解压体积。
- 异常压缩比。
- 加密成员。
- 路径穿越。
- 损坏压缩结构。
- XLSX 最大扫描单元格数量。

每个 Agent Run 对相同附件字节和解析结果做内存缓存，并用 single-flight 避免并发重复下载或
解析。缓存生命周期不跨 Run，不引入长期文件副本。

## 6. 原生模态与 PDF

以下文件不使用附件读取工具：

- 图片：要求 `input_modalities` 包含 `image`。
- 音频：要求包含 `audio`。
- 视频：要求包含 `video`。
- PDF：要求包含 `pdf`。

不支持时在模型调用前给出明确错误。PDF 本轮刻意不做文本解析或 OCR；后续应评估统一文档
解析服务，而不是把简单 PDF 抽文本继续塞进当前进程。

OpenAI Compatible 视频模型增加 `video_url` 请求适配。是否启用只依据模型配置的
`video` 模态或 `video_url` tag，不根据模型名称猜测。

## 7. 与上下文压缩的协同

原始事实源保持：

```text
chat_messages
  ├─ 用户附件引用
  ├─ 模型工具调用
  └─ 每一页工具结果
```

压缩与去重规则：

- 工具读取键包含 `tool_name + file_id + sheet + offset + limit`。
- 同一文件同一页重复读取可以去重。
- 不同 offset、limit 或 sheet 不能误判为重复。
- 长工具结果仍参与 Clamp、ClearToolResults 和摘要治理。
- 只有摘要成功且达到目标预算时才更新 Checkpoint。
- Checkpoint 更新不修改原始附件引用或原始工具事件。

因此压缩后模型仍能从原始会话范围重新读取附件，但不会把所有已读文件内容永久堆进每轮
输入。

## 8. 数据与 Checkpoint

平台文件服务保存文件字节，DeepAgent 只持久化稳定引用：

```json
{
  "kind": "attachment-ref",
  "id": "file-demo-123",
  "name": "合同.docx",
  "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
}
```

模型的 `tool-call` 和每一页 `tool-return` 继续作为完整原始事件写入
`chat_messages.payload`。工具结果包含 `file_id`、分页范围、解析文本和继续读取游标。

`chat_context_checkpoints.payload` 只是下一轮模型输入的派生投影：

- 只保留安全附件引用，不保存文件字节。
- 不保留旧 `extracted_text`。
- 原生媒体内容替换为“内容已省略”的文本占位。
- Provider 元数据、认证信息和临时签名 URL 被移除或脱敏。
- 可靠摘要可保留文件 ID、已读范围和关键结论。

每轮顺序必须是：

```text
读取完整原始历史
→ 持久化当前用户附件引用
→ 从完整历史建立会话附件白名单
→ 加载 Checkpoint 与其后的原始尾部
→ 执行压缩和模型调用
```

不能从 Checkpoint 建附件权限白名单，否则被摘要覆盖的旧附件会失去可读性。Checkpoint 损坏、
版本不符或哈希不匹配时，可以放弃投影并回退完整原始历史。

## 9. 标题与兼容

- 新消息不再发送 `extracted_text`。
- 历史消息中已有的 `extracted_text` 仍可反序列化和回放，避免破坏旧会话。
- 会话标题从 `<user>...</user>` 包装中提取真实用户请求，避免把大段引用上下文当标题。
- 标题模型解析优先使用运行器已经持有的 Store，减少隐式全局上下文依赖。

## 10. 验证边界

- 三个工具默认调用只传 `file_id` 可以成功。
- `offset/limit/has_more/next_offset` 连续分页正确。
- 未属于当前会话的 `file_id` 在下载前被拒绝。
- 同一 Run 重复读取只下载和解析一次。
- DOCX 页眉、正文段落、表格、页脚、批注顺序正确。
- XLSX 首个可见工作表、显式 sheet 和分页正确。
- UTF-16、UTF-8、GB18030 和 JSON 文本正确。
- Office 损坏、加密、路径穿越和压缩异常给出稳定错误。
- 文件 ID、MIME 和分页范围错误使用 `ModelRetry`；下载失败、文件损坏、解析失败和超过读取上限使用 `ToolFailed`。
- Agent 未选择 `builtin:attachment` 时工具列表为空；选择后加载包内三个工具。
- 图片/音频/视频/PDF 只服从模型原生模态。
- 真实文件解析、API → Tool → SSE → 持久化 → 会话续读链路通过。
- 附件工具结果参与上下文预算，不同页面不被误去重。
- 完整 Python、Console Node、SQLite Alembic 和 `git diff --check` 通过。

## 11. 实现地图

| 范围 | 主要路径 |
| --- | --- |
| 附件引用、会话索引与原生模态 | `deep_agent/agent/attachments.py` |
| TXT/DOCX/XLSX 解析和 Office 安全 | `deep_agent/agent/attachment_extractors.py` |
| 上传处理 | `deep_agent/agent/media_pipeline.py` |
| 三个读取工具与单 Run 缓存 | `deep_agent/capabilities/attachments/` |
| Runner 与附件白名单 | `deep_agent/agent/runner.py` |
| 压缩去重键 | `deep_agent/agent/context/file_keys.py` |
| 上传 API | `deep_agent/api/v1/uploads.py` |
| Console 上传与历史展示 | `frontend/console/chat.js` |

## 12. 安全与资源边界

后续修改必须守住：

- `file_id` 必须来自当前 Agent 会话完整历史或当前请求。
- 平台下载只能使用当前用户身份，模型不能传自定义 Header 或 URL。
- Office 解压和扫描继续受成员数、解压体积、压缩比和单元格数量限制。
- 三个工具保持统一 `offset/limit/has_more/next_offset` 协议。
- 同页可以去重，不同页或工作表不能误去重。
- Tool Call/Result 在裁剪和摘要分块中不能被拆成非法配对。
- `builtin:attachment` 默认关闭；旧编码只在 `002` 迁移中转换。
- 不恢复上传时预抽全文或 `DEEP_AGENT_UPLOAD_EXTRACT_TEXT_MAX_CHARS`。

## 13. 后续方向

后续方向包括统一文档解析服务、PDF/OCR、PPTX、复杂 Office、异步解析、资源隔离、结构
索引、跨 Run 缓存和长结果外置。优先级和状态统一维护在
[`../TODO.md`](../TODO.md)，不在本文维护第二份待办。
