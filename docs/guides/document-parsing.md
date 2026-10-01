# 文档解析

在「全局配置 → 常规 → 工具设置 → 文档解析」选择供应商并填写其 Key，再在 Agent 的内置工具中关联「文档解析」（`builtin:document`）。现有 Agent 不会自动启用。

默认编辑选项是 MinerU；未填写 Key 时不能解析。配置使用现有 `global_setting` 的 `document_parsing` 记录，只有当前选中的供应商和 Key 会保存。切换供应商时弹窗保留各自草稿，关闭后清除；编辑已配置的供应商而未改 Key 时保留原 Key。读取配置只返回设置状态和掩码。

| 供应商 | 实际入口与请求模型 | 当前声明支持的格式 |
| --- | --- | --- |
| MinerU | 官方精准 API，`model_version=vlm` | PDF、PNG、JPEG、DOC、DOCX、PPT、PPTX、XLS、XLSX |
| PaddleOCR | AI Studio OCR jobs，`PaddleOCR-VL-1.6` | PDF、PNG、JPEG |
| GLM-OCR | 官方 `layout_parsing`，`glm-ocr` | PDF、PNG、JPEG |

没有快速／精准切换、供应商自动回退或额外环境变量。服务名称、限制和格式由同一能力表提供给配置界面、上传入口、工具目录和模型说明；不混用另一托管入口的能力。保存仅检查结构和必填项；「测试连接」会实际解析一页内置样例并核对已知文字和金额，会消耗供应商额度。

配置在下一次 Run 生效，已经开始的 Run 保持原配置。原有上传类型仍可用；上传白名单留空时增加当前供应商支持的类型，已明确设置的白名单、大小及数量限制继续执行。上传被策略拒绝时不会发送给供应商。

## 工具调用

`read_file` 是快速直接读取文本、DOCX 和 XLSX 的系统工具。`read_document` 是专业 OCR、版面及复杂表格解析工具，必须显式启用；通常耗时更长，识别结果仍可能出错。

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

与 `read_file`、`read_tool_result` 共用预算：模型窗口小于 256000 Token 或未知时为 **24000 估算 Token**，达到 256000 时为 **48000**。正文、页面信息、概览和续读参数一起计入预算；不再额外设页数、行数或字符返回上限，也不二次进入大工具结果外置。整体模型请求仍执行现有上下文检查与压缩。

小结果即使有 8 页，只要整体在预算内，就一次返回全部 8 页。以下示例只示意两页：

```json
{
  "document":{"type":"document","document_id":"doc_example"},
  "provider":"mineru","model":"vlm","status":"complete",
  "summary":{"source_pages":2,"parsed_pages":2,"page_numbers_known":true,"missing_pages":[],"coverage":"complete"},
  "pages":[
    {"page_number":1,"format":"markdown","content":"原生第一页正文","char_offset":0,"char_end":7,"complete":true},
    {"page_number":2,"format":"markdown","content":"原生第二页正文","char_offset":0,"char_end":7,"complete":true}
  ],
  "warnings":[],"offset":1,"char_offset":0,"limit":2,"returned_count":2,
  "has_more":false,"next_read":null
}
```

大结果返回能装下的完整页面。单页过大时在 Unicode 字符边界切分，保留页码、`char_offset` 和 `char_end`，不改写源 Markdown 来补闭合标记。`complete` 表示当前片段已读到该页末尾。模型将返回的 `next_read` 原样交给同一工具即可：

```json
{
  "resource":{"type":"document","document_id":"doc_example"},
  "offset":8,
  "char_offset":12000
}
```

该例仍续读所选页面序列的第 8 页，不会再次请求供应商。页范围和显式 `limit` 如果存在，也会带在 `next_read` 中。供应商没有可靠物理页码时，页码和覆盖明确标为未知，按序列续读，不伪造物理页；此时不能使用原始页范围筛选。

`status` 表示供应商解析是否完整，`has_more` 只表示当前缓存批次是否读完。缺页时即使 `has_more=false`，也保留 `partial`、缺页信息和警告。`summary` 是结构概览，工具不会额外调用模型生成内容摘要或修正 OCR 金额、日期。

## 等待、错误与诊断

三家通过异步 HTTP 调用，默认 5 秒轮询，总等待上限 900 秒。成功提交后只查询或下载原任务；提交结果不明确时报告未知状态，避免自动再次计费。取消停止本地等待，Run 结束、失败、等待人工操作或取消后清理临时缓存、任务和 HTTP 资源，不承诺远端任务已停止或停止计费。

错误分别说明凭据无效、权限不足、额度／限流、文件格式或大小、远端任务失败、下载失败和结果无效。诊断日志关联会话、Run 和工具调用，记录供应商、接口方法／路径、安全参数、Hash、字节数、阶段耗时、HTTP 状态、页覆盖、缓存命中及返回 Token；不记录合同正文、原生响应、Base64、Key 或签名 URL。完整原生响应只保存在当前 Run 的私有临时目录。

实现与三家真实合同回归见 [文档解析课题](../design/37-document-parsing/README.md)。接口成功、页数完整和业务金额填报正确分别验证；供应商原生错误不会在工具里加入租赁专用修正规则。
