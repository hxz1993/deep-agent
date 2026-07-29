# 附件解析

> 首次研究：2026-07-30  
> 当前状态：第一版已实现，后续继续补统一文档解析与 PDF/OCR

本课题记录 DeepAgent 附件从“上传时截断抽取文本”改造成“会话内按需工具读取”的设计、
实现边界和后续演进。

第一版结论：

- 图片、音频、视频和 PDF 属于模型原生输入，是否发送只认
  `llm_models.input_modalities`。
- TXT、CSV、JSON、Markdown、HTML、XML、DOCX 和 XLSX 由 Agent 显式选择
  `attachment` 能力后按需读取。
- 工具首次调用通常只传 `file_id`；返回 `has_more` 和 `next_offset` 后，模型再决定是否读取
  下一页。
- 新上传不再预抽文本，也不再使用 `DEEP_AGENT_UPLOAD_EXTRACT_TEXT_MAX_CHARS`。
- `chat_messages` 保存原始附件引用和完整工具事件；Checkpoint 只保存模型输入的派生投影。
- PDF 本轮不增加本地解析或 OCR。文本模型无法读取 PDF 时返回明确错误。

详细方案与验证口径：

- [附件工具合并与运行设计](attachment-tool-integration-plan.md)

