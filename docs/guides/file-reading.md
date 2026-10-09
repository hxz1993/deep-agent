# 文件读取与核验

`read_file`仍是根据当前会话附件或已授权Skill资源自动提供的系统工具。没有新增工具名、引擎切换配置或环境变量。`parse_document`继续显式启用，负责当前供应商支持的PDF和图片OCR，本轮不改其请求和结果适配。

| 文件 | 引擎与可读取内容 | 需要注意 |
| --- | --- | --- |
| 文本、CSV、JSON、日志 | 现有解码器；文本行、JSON原有整理 | CSV内容需要按CSV规则理解，不能用逗号硬拆列 |
| DOCX | MarkItDown正文；python-docx补页眉、页脚、署名批注 | 保留链接、脚注、标题与列表；图片文字、文本框、原布局和合并关系仍可能缺失 |
| XLSX | openpyxl解码；安全OOXML扫描真实内容范围；CSV表达 | 保留文本类型、错误码、缓存公式和物理行；常见百分比、补零、千分位格式可显示；复杂格式提供原值与格式引用，不重算公式 |
| PPTX | MarkItDown本地转换器；幻灯片编号、文本、表格、支持的图表数据、备注 | 图片引用未做OCR；不保证原布局、合并关系及所有图表类型 |
| PDF文本层 | MarkItDown本地PDF转换，按物理页保留位置 | 没有文本的页仍保留页码；不做OCR，文字层存在也不证明图片、手写或版面已完整读取 |
| 扫描PDF、PNG、JPEG | 已启用且支持该格式的`parse_document` | OCR不保证细节正确；完整页数不证明页面内没有遗漏 |

旧二进制DOC/XLS/PPT未声明支持。MarkItDown只装DOCX/PPTX/PDF扩展，不启用LLM或OCR插件，不暗中收费或自动上传文件。上传白名单、大小限制和Skill资源授权继续生效。

## 使用与续读

```json
{"resource":{"type":"attachment","file_id":"file_123"}}
```

XLSX可指定`sheet`，省略时读取第一个可见工作表。一次调用读取一个工作表，读完整工作簿须按目录逐表读取；隐藏工作表真实状态保留。`headers`只是第一物理行的预览，不猜测业务表头。

返回仍包含`content/metadata/offset/char_offset/limit/returned_count/total_count/has_more/next_offset/next_char_offset`。完整返回包装共用24K/48K估算Token预算；先转换内容，再计算预算，不限制默认项数。续读必须同时复制两个位置。文本按行，DOCX/PPTX按转换后的固定逻辑块，XLSX按源工作表物理行，PDF按原文件物理页（`format=pdf`、`unit=page`）；单项很长时使用项内Unicode字符位置。

PDF首次只传文件引用即可，后续将`next_offset/next_char_offset`作为`offset/char_offset`继续读取；`limit`表示最多读取多少原始页。没有提取到文本的页保留提示，不能解释为原件空白；无需重复本地读取，可按当前已加载工具的能力使用专业OCR。

XLSX正文是经过引用转义的CSV，单元格换行不代表新的源行。`metadata.merged_ranges`列出与当前返回物理行相交的原始合并范围。`unsupported_number_formats`是未知显示格式字典，正文以`[F1]`等引用；不能将该标记当作源文字。`missing_formula_cache_count`说明未缓存的公式结果，不能把留空当作公式结果为零。常见显示舍入时附原值，保留计算精度。

## 核验Skill

内置“文件读取与核验”（`file-reading-verification`）需Agent显式关联；不自动启用工具，也不授予文件或脚本权限。它指导按任务读取、续读并核验关键编号、金额、百分比、日期及表格关系。

关键原则：OCR未识别到不等于原件没有；关键内容缺失或冲突应定向看原图或核对源单元格，无法访问原件时标记未知。普通概览不强制多引擎交叉验证。仅在已有执行工具和合法数据可用时做必要计算，不假定沙箱能直接访问附件路径、联网或安装包。
