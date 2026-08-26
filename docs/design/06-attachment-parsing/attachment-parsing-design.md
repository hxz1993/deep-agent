# DeepAgent 统一文件解析与按需读取设计

> 状态：v0.3.23 已统一附件与 Skill 文件入口
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
  → 当前会话有可读附件时自动注入 read_file
  → 模型按来源调用统一读取工具
  → 工具返回安全大小的一页和继续读取游标
```

Skill文件使用相同工具但以 `skill_code/path`标识，并只允许当前Agent已选择且运行就绪的资源。
模型不需要预先猜 `offset`和 `limit`。发现 `has_more=true`后才使用
`next_offset` 继续读。这与 Pi Agent、Hermes、OpenCode 等工具型 Agent 的渐进式文件读取方向
一致：工具提供合理默认页，不让模型在首次调用时承担文件规模判断。

## 2. 系统工具与默认行为

系统工具目录提供：

```text
code: runtime:file
tool: read_file
configurable: false
```

它不属于 `agents.capabilities`：当前或历史会话存在可读附件时自动披露；Agent选择的Skill含可读
资源时同样披露。两类来源都不存在时由 `PreparedToolset`隐藏。Alembic `004`从所有显式能力列表
中清理旧 `builtin:attachment`，但不重写历史 Tool Call/Result。

## 3. 工具协议

### 3.1 请求与通用响应

附件请求：

```json
{"resource":{"type":"attachment","file_id":"platform-file-id"}}
```

Skill请求：

```json
{"resource":{"type":"skill","skill_code":"example","path":"assets/template.xlsx"}}
```

统一返回：

```json
{
  "source": "attachment",
  "resource": {"type":"attachment","file_id":"platform-file-id"},
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

### 3.2 文本

```text
read_file(resource, offset=None, limit=None)
```

- 单位：物理行。
- 默认：500 行。
- 硬上限：2,000 行。
- 支持：TXT、CSV、JSON、Markdown、HTML、XML。
- 编码：UTF-16 BOM、UTF-8 BOM、UTF-8、GB18030，最后才使用替换字符兜底。
- 只有明确 JSON MIME 或 `.json` 文件名才格式化 JSON，不根据内容猜格式。

### 3.3 DOCX

```text
read_file(resource, offset=None, limit=None)
```

- 单位：逻辑块。
- 默认：100 块。
- 硬上限：500 块。
- 顺序：页眉 → 正文段落/表格 → 页脚 → 批注附录。
- 表格转成可读行，正文段落与表格保持文档内出现顺序。
- DOCX内嵌图片不进入 `read_file`返回；本工具只返回可分页的文本与表格结构。

第一版不承诺：

- 版面、分页、浮动对象和样式还原。
- 修订前后版本重建。
- 图片与具体段落的精确定位。
- 嵌入对象、宏和复杂域代码执行。

### 3.4 XLSX

```text
read_file(resource, sheet=None, offset=None, limit=None)
```

- 单位：物理行，包含表头。
- 默认：100 行。
- 硬上限：500 行。
- 未指定 `sheet` 时选择第一个可见工作表。
- 显式工作表名不区分大小写。
- 返回工作表列表、行列数、表头等元数据。
- 公式只读取文件中已有的缓存值，不在服务端计算。

## 4. 会话与权限边界

附件只接受当前 `(agent_id, session_id)`范围内出现过的 ID：

```text
当前消息 attachments[]
  + 当前会话原始 user/message Event 中的 attachment-ref
  → 本轮 attachment_by_id 白名单
```

未知 `file_id` 在平台下载前直接拒绝。平台下载仍使用当前请求的认证 Header，因此同时具备：

1. DeepAgent 会话范围限制。
2. 平台当前用户文件权限限制。

摘要Evidence不能作为附件权限来源；允许范围必须从完整原始Event建立。

Skill文件必须来自当前Agent已选择、启用且运行目录就绪的Skill，并精确命中安装资源索引；脚本、
目录穿越、宿主绝对路径与跨Skill读取均拒绝。

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

每个Agent Run对相同附件或Skill文件的字节和解析结果做内存缓存，并用single-flight避免并发重复
下载、读盘或解析。这里的Run是用户发送一次消息后，直到 `final/pending/cancelled/error` 的完整
模型与工具循环；下一轮重新加载，不引入跨Run文件缓存。

## 6. 原生模态与文件工具

原生输入边界：

- 当前轮图片、音频和视频附件直接作为原生多模态内容送入Provider，不经过 `read_file`。
- DeepAgent不再根据 `llm_models.input_modalities`建立一层本地拒绝；该字段保留为模型目录与管理员
  说明，实际协议兼容性由Provider决定。
- 所选模型不支持相应内容时，Provider可以返回拒绝说明或接口错误，沿现有模型错误链路结束Run。
- 历史媒体和Skill媒体不重新下载或读取。
- PDF与PPT/PPTX既不冒充模型原生模态，也不进入普通 `read_file`；未来有明确需求时，作为独立
  文档解析工具设计，允许针对页面、版面、图片与结构建立专用结果协议。

OpenAI Compatible 视频模型增加 `video_url` 请求适配。是否启用只依据模型配置的
`video` 模态或 `video_url` tag，不根据模型名称猜测。

## 7. 与上下文压缩的协同

原始事实源保持：

```text
chat_session_events
  ├─ 用户附件引用
  ├─ 模型工具调用
  └─ 每一页工具结果
```

压缩与去重规则：

- 工具读取键包含 `source + file_id/skill_code/path + sheet + offset + limit`。
- 同一文件同一页重复读取可以去重。
- 不同 offset、limit 或 sheet 不能误判为重复。
- 超大工具结果优先Spill；摘要输入副本只对单条超大内联结果做头尾裁剪。
- 只有八段摘要、Evidence、来源Hash和目标预算都通过时，Compaction Event才更新为Completed。
- Compaction不修改原始附件引用或工具Event。

因此压缩后模型仍能从原始会话范围重新读取附件，但不会把所有已读文件内容永久堆进每轮
输入。

## 8. 数据与Compaction

平台文件服务保存文件字节，DeepAgent 只持久化稳定引用：

```json
{
  "kind": "attachment-ref",
  "id": "file-demo-123",
  "name": "合同.docx",
  "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
}
```

模型的完整Assistant Message（含Tool Call）与完整Tool Results Message分别写入
`chat_session_events`。工具Part包含`file_id`、分页范围、解析文本和继续读取游标，并通过
`tool_call_id`在投影层配对。Compaction的摘要锚点：

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
→ 加载最新Completed Compaction锚点与其后的原始尾部
→ 执行压缩和模型调用
```

不能从摘要Evidence建附件权限白名单。Compaction版本不符、来源Hash不匹配或校验失败时保持上一次
有效Surface；原始Event始终可用于授权回查。

## 9. 标题与兼容

- 新消息不再发送 `extracted_text`。
- 历史消息中已有的 `extracted_text` 仍可反序列化和回放，避免破坏旧会话。
- 会话标题从 `<user>...</user>` 包装中提取真实用户请求，避免把大段引用上下文当标题。
- 标题模型解析优先使用运行器已经持有的 Store，减少隐式全局上下文依赖。

## 10. 验证边界

- `read_file(resource={type: "attachment", file_id})`默认调用可以成功。
- `offset/limit/has_more/next_offset` 连续分页正确。
- 未属于当前会话的 `file_id` 在下载前被拒绝。
- 同一 Run 重复读取只下载和解析一次。
- DOCX 页眉、正文段落、表格、页脚、批注顺序正确。
- XLSX 首个可见工作表、显式 sheet 和分页正确。
- UTF-16、UTF-8、GB18030 和 JSON 文本正确。
- Office 损坏、加密、路径穿越和压缩异常给出稳定错误。
- 文件 ID、MIME 和分页范围错误使用 `ModelRetry`；下载失败、文件损坏、解析失败和超过读取上限使用 `ToolFailed`。
- 即使 `capabilities=[]`，存在合法可读附件时仍自动披露系统工具 `read_file`；无文件来源时隐藏。
- 当前轮图片、音频和视频直传Provider，模型不支持时由Provider返回错误；历史和Skill媒体不读取。
- 真实文件解析、API → Tool → SSE → 持久化 → 会话续读链路通过。
- 附件工具结果参与上下文预算，不同页面不被误去重。
- 完整 Python、Console Node、SQLite Alembic 和 `git diff --check` 通过。

## 11. 实现地图

| 范围 | 主要路径 |
| --- | --- |
| 附件引用、会话索引与原生模态 | `deep_agent/agent/attachments.py` |
| TXT/DOCX/XLSX 解析和 Office 安全 | `deep_agent/agent/attachment_extractors.py` |
| 上传处理 | `deep_agent/agent/media_pipeline.py` |
| 统一文件工具与单 Run缓存 | `deep_agent/files/`、`deep_agent/agent/deps.py` |
| Runner 与附件白名单 | `deep_agent/agent/runner.py` |
| 压缩去重键 | `deep_agent/agent/context/file_keys.py` |
| 上传 API | `deep_agent/api/v1/uploads.py` |
| Console 上传与历史展示 | `frontend/console/chat.js` |

## 12. 安全与资源边界

后续修改必须守住：

- `file_id` 必须来自当前 Agent 会话完整历史或当前请求。
- 平台下载只能使用当前用户身份，模型不能传自定义 Header 或 URL。
- Office 解压和扫描继续受成员数、解压体积、压缩比和单元格数量限制。
- 附件与Skill文件保持统一 `offset/limit/has_more/next_offset`协议。
- 同页可以去重，不同页或工作表不能误去重。
- Tool Call/Result 在裁剪和摘要分块中不能被拆成非法配对。
- `builtin:attachment`和三个旧附件工具已删除；004只清理显式Agent能力中的旧编码，不改历史。
- 不恢复上传时预抽全文或 `DEEP_AGENT_UPLOAD_EXTRACT_TEXT_MAX_CHARS`。

## 13. 后续方向

后续方向包括PDF/PPTX专用解析、复杂Office、异步解析、资源隔离、结构
索引、跨 Run 缓存和长结果外置。PDF/PPTX的行业方案、Anthropic Skills目录、许可证边界和
DeepAgent建议链路已独立记录在
[`../16-pdf-and-presentation-parsing/`](../16-pdf-and-presentation-parsing/)。优先级和状态统一维护在
[`../TODO.md`](../TODO.md)，不在本文维护第二份待办。
