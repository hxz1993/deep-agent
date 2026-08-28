# PDF 与演示文稿解析调研

## 1. 结论

PDF和PPT/PPTX不是普通文本文件。DeepAgent不应把它们继续塞进当前`read_file`，也不应把
Pydantic AI的`DocumentUrl`当作本地解析器。建议保持三层职责：

```text
平台文件与授权
→ DeepAgent解析/渲染工具
→ 模型理解

文本层：确定性抽取，便宜、可分页
视觉层：页面/幻灯片渲染，保留图表和空间关系
OCR层：只在扫描件或文本层不足时启用
```

阅读、编辑和生成也应分开：第一版只解决“理解已有文档”，不同时建设PDF编辑器、PPT生成器或
模板加工流水线。

## 2. 为什么解析困难

### 2.1 PDF

同一扩展名可能代表完全不同的内容：

- 有可靠文本层的电子PDF。
- 只有扫描图片的PDF。
- 文字位置决定语义的复杂表格、双栏报告和财务报表。
- 主要由图片、图表或矢量图形组成的页面。
- 带AcroForm、批注、签名、加密或损坏交叉引用的文件。

单纯`extract_text()`能回答正文类问题，但不能证明表格列关系、页面布局、图中标注或表单状态正确。

### 2.2 PPT/PPTX

PPTX是包含多份OOXML、关系和媒体文件的ZIP包。一页的真实含义可能来自：

- 多个文本框和富文本Run。
- 图表、SmartArt、组合形状和连接线。
- 图片、截图、嵌入对象和公式。
- 母版、版式、主题、动画和讲者备注。

只抽文字适合摘要和检索，但无法可靠回答“这张图表达什么”“左右两组数据是什么关系”。旧`.ppt`
还需要先通过LibreOffice等工具转换成`.pptx`。

## 3. 其他项目的实际做法

| 项目 | PDF路径 | PPT/PPTX路径 | 适用边界 |
| --- | --- | --- | --- |
| Codex当前PDF/Presentations Skills | `pdfplumber`/`pypdf`抽取，Poppler逐页渲染并视觉检查 | 结构化演示工具、逐页渲染、Montage和溢出检查 | 本地工作区中的工具编排与严格QA，不是线上统一解析服务 |
| Anthropic Skills | `pypdf`、`pdfplumber`、Poppler；扫描件转图片后OCR | MarkItDown按页抽文字，缩略图理解版面，OOXML编辑，LibreOffice渲染与校验 | 完整操作手册和脚本集合；专有许可证，不能直接移植 |
| DeepSeek Harness | 只正式持久化PNG/JPEG/WebP/GIF；文件工具读取UTF-8文本 | 未提供 | 主动把PDF、PPTX和通用文件留作后续能力 |
| Pydantic AI | 用`DocumentUrl`、`BinaryContent`或`UploadedFile`交给支持文档的Provider | 没有通用PPTX本地解析 | Provider协议适配，不替应用抽取内容 |
| Pydantic AI Harness | 未提供PDF/PPTX产品级解析Capability | 未提供 | 现有Skill、文件和Sandbox能力只能作为组装原语 |
| DeerFlow | 优先PyMuPDF4LLM转Markdown；稀疏或失败时回退MarkItDown | DOCX/XLSX/PPTX统一由MarkItDown转Markdown | 线程工作区的快速文本投影，不保证视觉语义 |
| Letta | MarkItDown本地解析或Mistral OCR，再分块、Embedding | MarkItDown抽取后进入同一知识库链路 | 面向RAG和检索，不追求版面复原 |
| DeepAgents | `read_file`把PDF二进制作为Provider文件Block；不支持时替换为文字提示 | 对已知支持非PDF文件Block的OpenAI/Google Provider放行PPTX | 依赖Provider能力，不是本地解析器 |
| Hermes Agent | Poppler把指定PDF页渲染成PNG后交给视觉模型 | 未形成同等级PPTX解析链路 | 保留视觉信息，但页面越多成本越高 |
| Pi/OpenHands等编码Agent | 文件进入本地工作区，由模型按需运行现成CLI或脚本 | 同左 | 依赖可写工作区和命令执行环境，不直接适用于线上DeepAgent主服务 |

### 3.1 Codex

当前Codex暴露的PDF Skill使用`pdfplumber`或`pypdf`做快速文本检查，并要求用Poppler将页面渲染
为PNG进行视觉核验；演示文稿Skill则把结构操作、逐页渲染、Montage、溢出检查和最终视觉QA
拆开。它们证明“Skill负责选择工具和流程”，而不是由模型天然理解所有Office二进制格式。

该结论来自当前Codex运行时公开的PDF和Presentations Skill说明；它们属于运行时工作流参考，
不作为DeepAgent的代码依赖或可移植解析服务。

### 3.2 DeerFlow

DeerFlow把上传文件保存在当前线程工作区，并可显式开启自动Markdown转换：

```text
PDF
→ PyMuPDF4LLM
→ 输出字符过少或解析失败时回退MarkItDown

DOC/DOCX/XLS/XLSX/PPT/PPTX
→ MarkItDown
```

大于1 MiB的转换放入线程池，避免阻塞事件循环。这个实现适合全文问答，但“回退MarkItDown”不
等于一定完成OCR；扫描PDF仍可能需要真正OCR或视觉模型。

### 3.3 Letta

Letta把解析器抽象成统一`FileParser`：

- `MarkitdownFileParser`把PDF、DOCX、PPTX、XLS/XLSX等转换成Markdown。
- `MistralFileParser`把文件以Base64 Data URL提交给Mistral OCR。
- 解析结果被分块、生成Embedding并写入知识库。

这套抽象值得借鉴，但它解决的是“长期知识检索”，不是精确还原PPT图表或PDF页面。

### 3.4 Pydantic AI与DeepSeek Harness

Pydantic AI只统一`DocumentUrl`、`BinaryContent`和`UploadedFile`等Provider输入协议。支持格式和
URL/字节传输方式仍由具体模型决定；文档明确说明，当模型不支持时，应用需要自行抽取文本。

DeepSeek Harness当前更保守：持久附件v1只接受四种常见栅格图片，文件工具只读取UTF-8文本，
PDF、音频、视频和通用文件生命周期明确留待以后；PPTX也没有产品级解析器。它适合作为“二进制
事实源和模型读取工具分离”的架构参考，不能直接为DeepAgent补齐PDF/PPTX。

## 4. Anthropic官方Skill目录

来源：

- [Anthropic PDF Skill](https://github.com/anthropics/skills/tree/main/skills/pdf)
- [Anthropic PPTX Skill](https://github.com/anthropics/skills/tree/main/skills/pptx)

2026-08-21核对的目录如下：

```text
skills/pdf/
├── LICENSE.txt
├── SKILL.md
├── forms.md
├── reference.md
└── scripts/
    ├── check_bounding_boxes.py
    ├── check_fillable_fields.py
    ├── convert_pdf_to_images.py
    ├── create_validation_image.py
    ├── extract_form_field_info.py
    ├── extract_form_structure.py
    ├── fill_fillable_fields.py
    └── fill_pdf_form_with_annotations.py

skills/pptx/
├── LICENSE.txt
├── SKILL.md
└── scripts/
    ├── __init__.py
    ├── add_slide.py
    ├── clean.py
    ├── thumbnail.py
    └── office/
        ├── helpers/
        ├── schemas/
        ├── validators/
        ├── soffice.py
        └── validate.py
```

PDF Skill的任务分层：

- `pypdf`负责合并、拆分、旋转、元数据和基础结构操作。
- `pdfplumber`负责文字与表格抽取。
- `pdftotext`、`qpdf`、`pdftk`承担确定性命令行操作。
- 扫描件先转图片，再用`pytesseract`OCR。
- 表单使用单独的`forms.md`和字段/边界验证脚本。

PPTX Skill的任务分层：

- 读取使用MarkItDown，并按幻灯片编号组织文字。
- `thumbnail.py`生成带页码的缩略图网格，用于理解整体版式。
- 新建使用PptxGenJS；编辑模板优先解压OOXML后定点修改。
- `soffice.py`通过LibreOffice渲染，`validate.py`检查Schema、关系、Content Type、图表和幻灯片。

## 5. Anthropic许可证边界

PDF和PPTX目录的`SKILL.md`均声明`license: Proprietary`，其`LICENSE.txt`进一步明确禁止：

- 从Anthropic服务中提取并在服务外保留副本。
- 复制这些材料。
- 基于这些材料制作衍生作品。
- 分发、再许可或转让。
- 逆向工程、反编译或反汇编。

因此，在没有公司与Anthropic另行签署授权协议的前提下：

- **不能**把该目录下载后修改成DeepAgent Skill。
- **不能**复制其Prompt、脚本、References或Assets到仓库。
- **不能**通过改名、翻译或局部改写规避衍生作品限制。
- **可以**研究其公开展示的产品分层，并独立使用PyMuPDF、pdfplumber、Poppler、MarkItDown、
  LibreOffice等第三方工具重新设计自己的实现；每个依赖仍需遵守各自许可证。

许可证原文：

- [PDF LICENSE.txt](https://raw.githubusercontent.com/anthropics/skills/main/skills/pdf/LICENSE.txt)
- [PPTX LICENSE.txt](https://raw.githubusercontent.com/anthropics/skills/main/skills/pptx/LICENSE.txt)

若未来确需直接使用目录内容，必须先由法务或采购核对公司与Anthropic的实际Agreement是否另行
授予复制、修改和分发权，不能只凭GitHub仓库公开可访问就判断可以商用。

## 6. DeepAgent建议边界

### 6.1 产品入口

后续有真实业务需求时，新增一个独立的文档系统工具，名称和最终协议在实施前确定。概念链路：

```text
当前会话附件或已授权Skill资源
→ 文档工具按File ID/精确资源路径取得文件
→ 选择PDF或PPT/PPTX适配器
→ 按页/幻灯片返回文字、结构或视觉内容
→ 大结果继续复用现有Artifact外置
```

它应按合法文件来源自动注入，不要求Agent配置工具包；执行时继续复检会话所有权、Agent Skill授权和
资源索引。普通`read_file`继续只处理文本、DOCX和XLSX。

### 6.2 PDF适配器

建议第一版：

1. PyMuPDF/PyMuPDF4LLM按页提取Markdown和基础页面元数据。
2. 用字符密度和抽取异常判断文本层是否可用，不直接对所有PDF做OCR。
3. 用户询问图表/版面或文本层不足时，按指定页渲染PNG。
4. 模型支持图片时返回页面图像Block；纯文本模型只返回已抽文字，并明确视觉信息未验证。
5. OCR作为后续可选适配器，需单独治理外部服务、费用、隐私和超时。

### 6.3 PPT/PPTX适配器

建议第一版：

1. PPTX通过MarkItDown按幻灯片抽取文字；讲者备注如成为必要信息，需另行验证或直接读取OOXML。
2. 通过LibreOffice将指定幻灯片渲染为图片，补足图表、SmartArt和空间关系。
3. 旧PPT先转换为PPTX，不在主解析器中维护两套结构逻辑。
4. 不在读取工具中加入编辑或生成；相关需求未来单独设计Skill与安全执行链路。

### 6.4 缓存与结果

- 复用当前Run级文件字节与解析single-flight缓存，同一页重复读取不重复下载或解析。
- 第一版不增加数据库解析表；只有跨Run重复解析成为真实成本时，再评估按`content_hash`缓存派生结果。
- 返回结果保留页码/幻灯片编号、总数、截断原因、内容Hash和是否经过OCR/视觉检查。
- 页面图像和长Markdown受模型上下文预算控制，超过阈值继续复用现有大工具结果外置。

## 7. 触发条件与验收建议

当前保持“有潜力”，满足以下任一条件再启动：

- 真实Agent需要从合同、招标书、研究报告等PDF提取正文、表格或页面证据。
- 真实Agent需要理解PPT图表、架构图、版式或讲者备注，而不是只保存模板。
- 业务Skill中的PDF/PPTX资产必须被模型读取才能完成任务。

启动后至少准备三类PDF和三类PPTX验收集：

- 文本型、扫描型、复杂表格/图表型PDF。
- 文字型、图表/SmartArt型、图片主导型PPTX。
- 同时验证纯文本模型与支持图片模型的差异、分页、取消、History、压缩、权限和大结果外置。

## 8. 参考来源

- [Anthropic Skills](https://github.com/anthropics/skills/tree/main/skills)
- [Pydantic AI多模态文档输入](https://pydantic.dev/docs/ai/core-concepts/input/)
- [DeerFlow文件转换实现](https://github.com/bytedance/deer-flow/blob/main/backend/packages/harness/deerflow/utils/file_conversion.py)
- [Letta文件解析器](https://github.com/letta-ai/letta/tree/main/letta/services/file_processor/parser)
- [DeepSeek Harness附件层](https://github.com/deepseek-ai/deepseek-harness/tree/main/packages/attachment)
- [DeepSeek Harness文件工具](https://github.com/deepseek-ai/deepseek-harness/tree/main/packages/fs/tool-fs)
- [LangChain DeepAgents文件中间件](https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/deepagents/middleware/filesystem.py)
- [Hermes Agent PDF attach实现](https://github.com/NousResearch/hermes-agent/blob/main/tui_gateway/methods_prompt.py)
- [Microsoft MarkItDown](https://github.com/microsoft/markitdown)
