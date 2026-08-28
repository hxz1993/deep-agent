# DeepAgent 模型目录、输入模态与能力标签

> 适用版本：0.2.2+

## 1. 两层数据

模型数据分成目录与运行配置：

```text
model_catalog
  models.dev 的管理员刷新快照，只用于浏览和预填

llm_models
  管理员确认后的运行配置，是 Agent 唯一事实源
```

两张表没有外键。刷新目录不会自动修改模型配置，Agent 运行时也不会查询目录或 models.dev。
客户私有模型可不经过目录，直接手工创建 `llm_models`。

## 2. 模态与能力标签

`llm_models.input_modalities` 只描述 LLM 原生输入：

```text
text
image
audio
video
```

`llm_models.tags` 描述其他模型或 Provider 能力：

```text
reasoning
tool_call
structured_output
temperature
open_weights
interleaved
```

这两类标签不能混用。自动注入的系统工具 `read_file` 也不是模型 tag。平台上传能力不等于模型
原生模态：

- 当前轮图片、音频和视频：直接作为原生多模态内容送给Provider。模型目录的
  `input_modalities`用于管理员识别预期能力，不作为DeepAgent本地拒绝开关；所选模型不支持时，
  由Provider返回拒绝说明或接口错误。
- PDF和PPT/PPTX不是当前模型模态或上传类型，也没有本地解析能力；未来如有需求，使用独立文档
  解析工具，不并入 `read_file`。
- DOCX、XLSX、TXT、LOG、CSV、JSON、Markdown、HTML、XML：平台只保存 `file_id`；合法附件存在时
  `read_file`自动注入并按需分页读取，不在上传时预抽文本，也不要求Agent额外选择工具包。
- 图片不通过 `read_file`；当前轮图片直接作为多模态输入，历史图片和Skill图片不读取。
- MCP、Skill、UI Tool：属于 Agent 工具能力，不属于模型输入模态。

附件工具和安全边界详见
[`../../design/06-attachment-parsing/attachment-parsing-design.md`](../../design/06-attachment-parsing/attachment-parsing-design.md)。

## 3. 模型 Token 属性

模型配置要求：

```text
context_window_tokens
max_output_tokens
```

- `context_window_tokens` 是输入与输出共享总窗口。
- `max_output_tokens` 是 Provider 声明的单次最大输出能力上限，不是每轮固定预留。
- 最大输出不得超过上下文窗口。

不保存 `max_input_tokens` 或固定输出预留。当前上下文压缩会结合最近历史 usage 和保守估算
动态预测本轮输出，不能简单用“总窗口减最大输出”作为输入预算。

v0.6.3起，实际进入模型请求的当前轮图片会按Provider/模型路由估算：DeepSeek v4 Vision使用
公开的14像素Patch、3:1下采样和单图384 Token上限公式，并从已规范化图片字节读取实际尺寸。
其他路由、URL图片或无法识别的图像继续按单个媒体1024 Token保守估算。该值只用于压力预判，
实际结算与后续预算校准仍以Provider Usage为准；附件引用元数据不会冒充已发送的图片字节。

## 4. 模型目录刷新

管理员调用：

```http
POST /api/v1/llm-models/catalog/refresh
```

流程：

```text
获取 models.dev catalog.json
→ 可选补充 labs 页面
→ Provider 白名单过滤
→ 标准化 Lab / Model / Provider
→ 完整校验
→ 单事务 DELETE + INSERT
```

任何失败都保留旧目录。系统不做启动同步、定时同步、保存模型时同步或 Agent 运行时同步。

部署环境无法访问models.dev时，管理员可在可联网环境下下载官方
`https://models.dev/catalog.json`，再通过Console“导入”或下列接口导入：

```http
POST /api/v1/llm-models/catalog/import
Content-Type: multipart/form-data

file=<catalog.json>
```

导入与联网刷新使用完全相同的解析、Provider白名单、标准化与原子替换逻辑。
文件必须是UTF-8 JSON，且不超过16 MiB；校验失败保留旧目录。导入不会修改
`llm_models`中已配置的Base URL、API Key或模型属性。

当前数据库目录可通过Console“导出”或下列接口导出：

```http
GET /api/v1/llm-models/catalog/export
```

导出文件使用与导入相同的`models/providers/labs`结构，可原样再导入。它只包含当前
Provider白名单和标准化规则已接受的目录项，不是models.dev原始全量文件的备份。

目录查询：

```http
GET /api/v1/llm-models/catalog?view=model
GET /api/v1/llm-models/catalog?view=provider
```

支持 `q/lab_id/provider_id/modality/offset/limit`。查询只读取数据库。

## 5. Provider 白名单

0.2.2 首版展示：

- OpenAI
- Anthropic
- Google
- DeepSeek
- Alibaba
- Moonshot AI
- Zhipu AI
- MiniMax
- xAI

OpenRouter、Azure、Bedrock、Vertex 和长尾 Gateway 暂不进入目录。白名单位于代码中，不提供在线配置。
因此models.dev即使已收录某个模型，若它只由白名单外的渠道提供，刷新和导入都不会展示。

## 6. 已移除的旧机制

以下内容不再用于生产运行：

- `deep_agent/agent/models/modality_data.json`
- `deep_agent/agent/models/models_dev_snapshot.json`
- `model_lookup.py`
- `modality_resolver.py`
- 模型名称或 Base URL 猜测模态
- 创建模型时自动联网补充 Meta
- `POST /api/v1/llm-models/refresh-metadata`
- `POST /api/v1/llm-models/{id}/refresh-metadata`
- 模型目录 `source=auto|online|bundled` 和 `refresh=true`

## 7. 关键代码

| 能力 | 路径 |
| --- | --- |
| 目录 ORM | `deep_agent/db/models.py` 的 `ModelCatalog` |
| 目录 Store | `deep_agent/db/stores/model_catalog/` |
| models.dev 获取 | `deep_agent/agent/models/models_dev.py` |
| 标准化与视图 | `deep_agent/agent/models/catalog.py` |
| API | `deep_agent/api/v1/llm_models.py` |
| 运行时模态 | `deep_agent/agent/attachments.py`、`input_capabilities.py` |
| Console | `frontend/console/configuration/catalog.js`、`llm-models.js` |
| 迁移 | v0.3.0 完整基线 `alembic/versions/001_initial_schema.py` |
| 配置备份 | `maintenance/config_backup.py` |

## 8. 验证清单

- 手工模型可以保存上下文、最大输出、输入模态和 tags。
- 模态与能力标签在 Console 分开显示。
- 目录为空时有明确空状态。
- models.dev 刷新失败时旧目录仍可查询。
- “使用此模型”仅预填，不自动覆盖已有模型配置。
- 模型名称不会触发图片兼容性猜测；当前轮图片统一交给Provider判断。
- 配置备份包含 `model_catalog` 和 `llm_models` 新增字段。
