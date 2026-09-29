# 16 · PDF 与演示文稿解析

> 首次研究：2026-08-21
>
> 当前状态：完成行业方案、Anthropic Skills、Codex、DeepSeek Harness与AI Labs源码调研；尚未实施

本课题研究 DeepAgent 后续如何理解 PDF、PPT 和 PPTX。它不扩充当前普通文件工具
`read_file`，也不把某个 Provider 的文档输入能力误写成所有模型共有的原生模态。

2026-09-29 新增 [37 · 文档解析选型](../37-document-parsing/README.md)：针对 MPDocBench-Parse、复杂数字表格及在线／自部署成本进行专项研究。本篇保留格式分类和接入架构依据；新模型、价格和实测结果以 37 课题为准。

当前结论：

- PDF需要区分文本层、表格/版面、扫描件OCR、页面视觉和表单结构。
- PPT/PPTX需要区分幻灯片文字、图表/SmartArt/图片、讲者备注、版式和结构编辑。
- 成熟项目普遍组合确定性解析、页面/幻灯片渲染和按需OCR或视觉模型，没有一套万能解析器。
- 后续如启动，应建设独立文档系统工具，复用现有附件与Skill授权、平台下载、Run级缓存和大结果外置；
  不在 `read_file` 中继续增加格式分支。
- Anthropic PDF/PPTX Skill可研究其任务分层和工具选择，但目录采用专有许可证，不能直接复制、
  修改、制作衍生版本或随DeepAgent分发。

完整调研、来源目录、许可证边界和建议链路见
[`pdf-and-presentation-parsing-research.md`](pdf-and-presentation-parsing-research.md)，排期状态见
[`../TODO.md`](../TODO.md)。
