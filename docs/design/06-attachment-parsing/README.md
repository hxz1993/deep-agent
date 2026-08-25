# 06 · 附件解析

> 首次研究：2026-07-30
> 当前状态：v0.3.24 已收口文件读取与原生多模态边界

本课题记录 DeepAgent 附件从“上传时截断抽取文本”改造成“会话内按需工具读取”的设计、
实现边界和后续演进。

第一版结论：

- 当前轮图片、音频和视频直接作为模型原生内容交给Provider；DeepAgent不根据模型目录预先拒绝，
  所选模型不支持时由Provider正常报错。
- TXT、CSV、JSON、Markdown、HTML、XML、DOCX 和 XLSX 由运行时系统工具 `read_file`按需读取，
  不需要 Agent配置工具包。
- 图片严格不经过 `read_file`：当前轮图片直接送入Provider；历史图片和Skill图片不读取。
- 工具首次调用通常只传附件 `file_id`或 Skill精确路径；返回 `has_more`和 `next_offset`后再读取
  下一页。
- 新上传不再预抽文本，也不再使用 `DEEP_AGENT_UPLOAD_EXTRACT_TEXT_MAX_CHARS`。
- `chat_session_events`保存原始附件引用和完整工具Event；Compaction只生成摘要锚点和来源索引。
- PDF和PPT/PPTX不作为模型原生模态或普通文件读取类型；当前无解析能力，未来有明确需求时另建
  专用文档解析工具。

详细方案与验证口径见
[`attachment-parsing-design.md`](attachment-parsing-design.md)。PDF/PPTX的专项调研见
[`../16-pdf-and-presentation-parsing/`](../16-pdf-and-presentation-parsing/)，排期状态统一登记在
[`../TODO.md`](../TODO.md)。
