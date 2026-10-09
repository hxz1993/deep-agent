# 文档解析

在「全局配置 → 常规 → 工具设置 → 文档解析」选择供应商并填写其 Key，再在 Agent 的内置工具中关联「文档解析」（`builtin:document`）。现有 Agent 不会自动启用。

当前只提供 PaddleOCR → GLM-OCR，未配置时默认编辑选项是 PaddleOCR。MinerU 的选项、请求适配和恢复代码已移除；旧配置读取为「未配置」并提示供应商无效，不自动改写存储或切换其他供应商。管理员须主动选择现有供应商并填写其 Key 后保存。未填写 Key 时不能解析。配置使用现有 `global_setting` 的 `document_parsing` 记录，只有当前选中的供应商和 Key 会保存。切换供应商时弹窗保留各自草稿，关闭后清除；编辑已配置的供应商而未改 Key 时保留原 Key。读取配置只返回设置状态和掩码。

| 供应商 | 实际入口与请求模型 | 当前声明支持的格式 |
| --- | --- | --- |
| PaddleOCR | AI Studio OCR jobs，`PaddleOCR-VL-1.6` | PDF、PNG、JPEG |
| GLM-OCR | 官方 `layout_parsing`，`glm-ocr` | PDF、PNG、JPEG |

没有快速／精准切换、供应商自动回退或额外环境变量。服务名称、限制和格式由同一能力表提供给配置界面、上传入口、工具目录和模型说明；不混用另一托管入口的能力。保存仅检查结构和必填项；「测试连接」会实际解析一页内置样例并核对已知文字和金额，会消耗供应商额度。

配置在下一次 Run 生效，已经开始的 Run 保持原配置。原有上传类型仍可用；上传白名单留空时增加当前供应商支持的类型，已明确设置的白名单、大小及数量限制继续执行。上传被策略拒绝时不会发送给供应商。

## 工具调用

`read_file`（读取文件）直接读取文本、DOCX、XLSX、PPTX及PDF文本层，不支持旧DOC/XLS/PPT或图片OCR。带文字层的PDF可先直接读取；扫描件、图片、乱码或不足以判断的版面关系使用已提供的`parse_document`（解析文档）。`parse_document`是专业OCR、版面及复杂表格解析工具，必须显式启用，格式以当前供应商为准；通常耗时更长，识别结果仍可能出错。常规文本及Office内容优先使用`read_file`。

原`read_document`在本轮改名为`parse_document`，参数、供应商和缓存续读不变，运行时不保留旧别名；已有历史工具调用按原存储事实展示。

```json
{"resource":{"type":"attachment","file_id":"file_123"}}
```

也支持已授权 Skill 的文件引用：

```json
{"resource":{"type":"skill","skill_code":"lease","path":"assets/contract.pdf"}}
```

| 参数 | 含义 |
| --- | --- |
| `resource` | 会话附件、已授权 Skill 文件，或本工具返回的当前 Run 文档引用 |
| `page_start` / `page_end` | 可选的原始页范围，从 1 开始、包含终点；只控制缓存展示，首次仍解析完整文件 |
| `limit` | 可选的最多返回页面数；省略时按 Token 预算尽量返回 |
| `offset` | 所选页面序列中的起点，默认 1 |
| `char_offset` | 超长单页的 Unicode 字符续读位置，默认 0；通常直接复制 `next_read` |

缓存只在当前 Run 有效。同一文件与配置的并发请求合并为一次解析，再次传原文件引用也复用结果；省略读取位置会从头读取，不会暗中移动游标。文档 ID、Hash 和缓存命中均不授予文件权限，每次读取继续检查原附件或 Skill 授权。

## 返回与续读

供应商原始响应、下载产物和原生页面保留在当前 Run 的私有缓存中。工具给模型返回的是固定的可读内容：普通 HTML 表格转 Markdown；合并、嵌套、混合表头及不能可靠转成 Markdown 的单元格（如嵌套强调）保留精简 HTML；异常表格或未知片段局部保留原文。删除显示样式、尺寸、边框和对齐属性，保留正文、空单元格、合并关系、标题、脚注、公式和图片引用。只有明确的原生表头才会成为 Markdown 表头，不猜测第一行含义；转换增加 Token 时保留精简 HTML。代码示例和未知标签不当成显示样式删除。

整页适配只执行一次，之后才计算 Token 和分页。`pages[].content` 是适配后的固定内容，`char_offset/char_end/next_read` 都基于该内容计算；不是原生 HTML 的字符位置。原始页码、缺页与解析覆盖仍取供应商事实。工具不纠正 OCR 数字、删除重复正文或猜测供应商未识别的内容；内嵌图片不返回 Base64，只保留明确说明，原始数据仍留在缓存。

Paddle 的原生 Markdown 可能默认忽略页眉、页脚、脚注和边注。工具同时检查供应商已经识别的原始布局块，保留原 Markdown，将未呈现的内容附在该页「原始布局补充」中，标出原始块序号和类型；补充块保持供应商给定顺序，不冒充原页版面顺序。GLM 的分页 Markdown 同样与布局块核对，并保留额外布局页。补回只恢复已识别信息，不重新 OCR、不改编号或金额；无法可靠证明相同的表达保守保留，可能增加重复内容及 Token。代码的语言 class 与未知结构保留，不当成展示样式删除。

与 `read_file`、`read_tool_result` 共用预算：模型窗口小于 256000 Token 或未知时为 **24000 估算 Token**，达到 256000 时为 **48000**。正文、页面信息、概览和续读参数一起计入预算；不再额外设页数、行数或字符返回上限，也不二次进入大工具结果外置。整体模型请求仍执行现有上下文检查与压缩。

小结果即使有 8 页，只要整体在预算内，就一次返回全部 8 页。以下示例只示意两页：

```json
{
  "document":{"type":"document","document_id":"doc_example"},
  "provider":"paddle","model":"PaddleOCR-VL-1.6","status":"complete",
  "summary":{"source_pages":2,"parsed_pages":2,"page_numbers_known":true,"missing_pages":[],"coverage":"complete"},
  "pages":[
    {"page_number":1,"format":"markdown","content":"可读第一页正文","char_offset":0,"char_end":7,"complete":true},
    {"page_number":2,"format":"markdown","content":"可读第二页正文","char_offset":0,"char_end":7,"complete":true}
  ],
  "warnings":[],"offset":1,"char_offset":0,"limit":2,"returned_count":2,
  "has_more":false,"next_read":null
}
```

大结果返回能装下的完整页面。单页过大时在适配后的固定内容的 Unicode 字符边界切分，保留页码、`char_offset` 和 `char_end`，不改写片段来补闭合标记。`complete` 表示当前片段已读到该页末尾。模型将返回的 `next_read` 原样交给同一工具即可：

```json
{
  "resource":{"type":"document","document_id":"doc_example"},
  "offset":8,
  "char_offset":12000
}
```

该例仍续读所选页面序列的第 8 页，不会再次请求供应商。页范围和显式 `limit` 如果存在，也会带在 `next_read` 中。供应商没有可靠物理页码时，页码和覆盖明确标为未知，按序列续读，不伪造物理页；此时不能使用原始页范围筛选。

`status/summary.coverage` 表示供应商的页面覆盖，`has_more` 只表示当前缓存批次是否读完。`complete` 不表示模型已读完正文，也不保证原页每个字段均被识别；需要全文审查或提取时，`has_more=true` 必须按 `next_read` 继续。限定页范围读完也不能声称已读整个文件。缺页时即使 `has_more=false`，也保留 `partial`、缺页信息和警告。`summary` 是结构概览，工具不会额外调用模型生成内容摘要或修正 OCR 金额、日期。

未识别到字段只能判为「识别结果未提供／待核对」，不能推断原件留空或无此字段。重要手写编号、金额、日期应核对原图；不确定字符须明确标记，不能把会签单等其他单据编号替换成原件正式编号。页面覆盖完整和业务字段已确认是两件事。

## 等待、错误与诊断

两家通过异步 HTTP 调用，Paddle 默认 5 秒轮询，GLM 等待同步响应，同一 Run、同一文件与配置累计等待上限 900 秒；恢复查询只使用剩余等待时间，超时后同一 Run 再次调用不会延长等待或重新提交。成功提交后只查询或下载原任务；提交结果不明确时报告未知状态，避免自动再次计费。取消停止本地等待，Run 结束、失败、等待人工操作或取消后清理临时缓存、任务和 HTTP 资源，不承诺远端任务已停止或停止计费。

错误分别说明凭据无效、权限不足、额度／限流、文件格式或大小、远端任务失败、下载失败和结果无效。诊断日志关联会话、Run 和工具调用，记录供应商、接口方法／路径、安全参数、Hash、字节数、阶段耗时、HTTP 状态、页覆盖、缓存命中及返回 Token。格式适配日志另记版本、表格、局部原样保留与布局补回数量、前后字符及正文估算 Token；正文估算不等于实际账单，分页日志仍计完整包装。不记录合同正文、原生响应、Base64、Key 或签名 URL。完整原生响应只保存在当前 Run 的私有临时目录。

实现与历史供应商实测记录见 [文档解析课题](../design/37-document-parsing/README.md)。接口成功、页数完整和业务金额填报正确分别验证；供应商原生错误不会在工具里加入租赁专用修正规则。
