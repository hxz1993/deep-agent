# 06 · 附件解析

> 首次研究：2026-07-30
> 当前状态：v0.3.23 已与 Skill 文件统一为系统 `read_file`；后续继续补 PDF/OCR

本课题记录 DeepAgent 附件从“上传时截断抽取文本”改造成“会话内按需工具读取”的设计、
实现边界和后续演进。

第一版结论：

- 图片、音频、视频和 PDF 属于模型原生输入，是否发送只认
  `llm_models.input_modalities`。
- TXT、CSV、JSON、Markdown、HTML、XML、DOCX 和 XLSX 由运行时系统工具 `read_file`按需读取，
  不需要 Agent配置工具包；支持图片输入的模型也可通过该工具读取附件或 Skill图片。
- 工具首次调用通常只传附件 `file_id`或 Skill精确路径；返回 `has_more`和 `next_offset`后再读取
  下一页。
- 新上传不再预抽文本，也不再使用 `DEEP_AGENT_UPLOAD_EXTRACT_TEXT_MAX_CHARS`。
- `chat_messages` 保存原始附件引用和完整工具事件；Checkpoint 只保存模型输入的派生投影。
- PDF 本轮不增加本地解析或 OCR。文本模型无法读取 PDF 时返回明确错误。

详细方案与验证口径见
[`attachment-parsing-design.md`](attachment-parsing-design.md)。后续统一文档解析、PDF/OCR
和大文件治理事项统一登记在 [`../TODO.md`](../TODO.md)。
