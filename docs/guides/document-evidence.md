# 文档定位信息与证据文件

`parse_document` 在原有 Paddle、GLM 解析和分页之上，增加布局读取及完整证据文件导出。默认仍返回现有精简 Markdown／表格，不上传文件。Agent 必须关联 `builtin:document`；来源授权、供应商配置快照和 Run 缓存规则不变。

## 调用方式

```json
{
  "resource": {"type": "attachment", "file_id": "当前会话已授权的文件ID"},
  "include_layout": true,
  "export_file": true
}
```

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| `include_layout` | `false` | 返回固定布局读取视图，附 Block ID、正文字符范围、页尺寸和供应商坐标 |
| `export_file` | `false` | 后端将全部已取得的解析证据序列化成 JSON，上传到当前 File Store |

两个开关独立：只要可读正文时都不传；只需要模型定位时开启布局；只需要文件交付时只开启导出；需要字段引用和前端下载时同时开启。`export_file` 是布尔值，没有 `auto` 模式。`parse_document` 始终跳过通用大结果外置，正文过大只分页，不会悄悄上传。

原参数 `page_start/page_end/limit/offset/char_offset` 保持不变。首次仍解析整个文件，原始页范围只控制读取。导出**所有已经取得的页面**，不受本次范围、页数限制、Token 分页或布局开关影响；供应商缺页时导出保留 `partial`，不伪称完整。

## 工具返回与完整文件的区别

工具返回增加 `reading_view`、`pages[].layout` 和 `export`，其余字段保持原义：

| 位置 | 内容 |
| --- | --- |
| `reading_view` | `readable` 为原有正文视图，`layout` 为固定带块引用视图 |
| `pages[].content` | 本批正文片段，仍可包含 Markdown、普通表格和精简 HTML |
| `pages[].layout` | 仅布局模式有值；页尺寸、坐标状态以及与本批正文相交的 Block 元数据 |
| `export` | 文件交付状态和确认成功后的文件引用；不代表 OCR 识别正确 |
| `has_more/next_read` | 当前所选范围是否需要续读；与文件是否导出、供应商是否缺页分别判断 |

布局模式尽量直接复用已适配正文，通过字符范围关联 Block，不再为每个 Block 重复发送全文。无法可靠匹配的原生块独立追加，标记 `mapping_status="appended"`；页内无定位依据的正文保留在 `unmapped_spans`。没有识别文字的图像等块使用系统生成的 `【布局块：供应商未返回文字】` 作为可分页位置，标记 `mapping_status="no_text"`；该标记不是 OCR 原文，导出的 `native_content` 仍为空。

完整证据文件同时保留固定布局视图、供应商原生页面正文和原生块正文／表格，便于复核；因此文件可能比模型读取内容大。不会把供应商整个 HTTP 响应直接上传。Base64、临时签名地址、运行机器路径和未白名单化的传输字段不导出。这里的原生正文是不做 Markdown 精简的正文，仍受上述传输信息清理规则约束。

## 续读、ID 和字符位置

小文件一次返回全部页面。大文件仍按当前 Agent 模型的 **24K／48K 估算 Token** 分页，正文、布局和完整包装共同计入。超长页或 Block 可以拆分，每个片段仍沿用整块 ID 和坐标。

```json
{
  "resource": {"type": "document", "document_id": "doc_example"},
  "offset": 3,
  "char_offset": 8200,
  "include_layout": true,
  "export_file": true
}
```

模型应原样复制工具给出的 `next_read`。它包含两个开关及原来指定的范围，不再次 OCR，也不重复上传。

- `document_id` 是本次解析的 Run 内引用。新 Run 不保证复用该 ID 或解析缓存。
- `block_id` 如 `p2:b7`，只在这个 `document_id` 内唯一；`p2` 表示第 2 个**输出页面序号**，不能据此猜物理页码。引用必须使用 `(document_id, block_id)`，长期交付另带证据文件引用。
- `page_number` 是已知原始物理页码；未知时为 `null`。`page_ordinal` 是结果中的序号，从 1 开始。
- `offset` 从当前选中的页面序列计数，从 1 开始；`char_offset`、`char_end` 和 Block 的 `char_start/char_end` 从整页固定视图计数，从 0 开始，区间为左闭右开。
- 完整 Block 的布局元数据不重复 `ordinal`、`mapping_status="exact"`。只有被分页切开的 Block 才附 `fragment_start/fragment_end`；这些仍是**整页**字符位置。无 `mapping_status` 表示精确字符关联。
- 切换 `include_layout` 后须从 `offset=1,char_offset=0` 开始，不能混用两份视图的字符位置。

字符数按 Unicode 码点计算，不是字节，也不是 JavaScript 的 UTF-16 单元。示例 `A🙂B` 中 `🙂` 的范围为 `[1,2)`；JS 必须使用 `Array.from(text).slice(start,end).join("")`，不能直接 `text.slice(start,end)`。对于分页片段，先减去 `pages[].char_offset` 再截取；组合附加符仍按各个码点计数，不按用户视觉上的一个字形计数。

## 坐标能够说明什么

`bbox=[x0,y0,x1,y1]` 使用供应商页面图像的左上角原点，x 向右、y 向下。页尺寸和坐标必须来自同一平面；不把 GLM 数值一律当作 0～1000 归一化坐标。Paddle 原生多边形在证据文件的 `polygon_points` 中保留，工具批次使用紧凑 bbox。

| 字段／状态 | 含义与前端处理 |
| --- | --- |
| `coordinate_space="paddle_processed_page_pixels"` | Paddle 预处理后页面像素平面，可能经过旋转、展平 |
| `coordinate_space="glm_page_pixels"` | GLM 返回的页面像素平面，尺寸取 `data_info.pages` 或一致的块尺寸 |
| `location_status="available"` | bbox 在供应商这个平面内有效，**不等于可以直接覆盖原 PDF** |
| `original_page_mapping="unavailable"` | 检测到 Paddle 展平或旋转，本轮无可靠原 PDF 映射，不直接叠框 |
| `original_page_mapping="unverified"` | 未证明供应商平面与当前 PDF 预览平面一致，不能当作已经验证的原件坐标 |
| `unknown_page`／`conflicting_page_size` | 页码未知或供应商尺寸冲突，不猜位置；冲突尺寸在 `reported_page_sizes` 中保留 |
| `missing_bbox`／`invalid_bbox`／`missing_page_size`／`outside_page`／`empty_bbox` | 块不能安全定位；保留原文和可用的供应商数值，界面显示不可定位 |

当前版本没有声称已验证原 PDF 映射的状态，也不包含配准算法。消费方可以先做“字段 → 原生 Block 文本”的关联；要叠到原 PDF，需额外证明两边渲染平面一致或取得可靠变换。仅在同一平面已经确认时才按显示宽高比例缩放 bbox。不能因为识别内容正确或 bbox 在页内，就忽略 `unavailable/unverified`。这也意味着接入证据接口不等于已完成租赁前端 PDF 联动验收。

## 导出状态、权限和文件下载

`export.status` 与正文成功分开：

| 状态 | 文件引用 | 行为 |
| --- | --- | --- |
| `not_requested` | 无 | 当前解析未请求导出 |
| `saved` | 有 | File Store 明确返回成功回执且 ID、文件名、字节数通过核验 |
| `failed` | 无 | 已知拒绝、身份缺失、大小超限、序列化或本地写入失败；正文仍能读取 |
| `unknown` | 无 | 上传已发起，但网络、取消或无效回执使保存结果不明确；不能假造可下载引用 |

同一 Run、同一解析的并发请求合并成一次上传，续读及再次请求导出复用状态和文件 ID。先只读、后来导出，复用已解析对象。`export_file=false` 不发起上传，但可返回此前已成功的文件引用。失败和结果未知会保留，模型重复调用不会自动重传；宿主只有在明确失败且原因修复后才能通过内部恢复接口重试，不重新 OCR。没有面向模型的重试参数，`unknown` 不允许盲目清空后重传。

只有 `saved` 才有 `export.file`，字段为：

| 字段 | 含义 |
| --- | --- |
| `storage` | `local` 或 `platform`，决定下载入口 |
| `file_id` | 文件服务实际返回的 ID；不是文档缓存 ID，也不是原 PDF 文件 ID |
| `file_name` | 后端生成的证据 JSON 文件名 |
| `byte_size`、`mime` | 实际 UTF-8 文件字节数；MIME 固定 `application/json` |
| `sha256` | **导出 JSON 原始字节**的 SHA256；不可先重新排版再计算 |
| `schema_version` | 当前为 `document-evidence-v1` |

导出使用当前服务端 File Store 及可信请求身份，没有用户可指定的上传地址或存储类型参数；沿用现有上传大小限制，不登记下载中心。Run 结束仅清理临时解析资源，成功导出的文件保留，其权限、保留与删除遵循原文件服务。

- `storage=local`：使用当前 DeepAgent 服务的 `GET /api/v1/local-files/{file_id}/download`，带当前会话身份；仅本地存储模式可用。`local_` ID 不能交给平台文件服务。
- `storage=platform`：通过当前平台已配置的文件服务 `/files/download?id={file_id}`，沿用现有鉴权与 App／Space 上下文。常见网关前缀为 `/deepfos-server/platform-file-server`，实际前缀由目标环境决定；不要硬编码 Alpha 域名。
- File ID、Hash、Block ID 都不授予访问权；工具每次续读仍验证原附件／Skill 来源，下载仍执行文件服务权限。

消费证据 JSON 时先按 `byte_size` 有界读取、核对 `sha256`，再严格解码 UTF-8 并校验 Schema。文件内部的 `source.sha256` 校验的是**原附件文件字节**，不是证据 JSON；`source.storage="skill"` 只描述源 Skill 资源，不改变导出文件引用中的存储类型。

Schema 之后还应核验 `document_id`、原文件引用／Hash、Block ID 唯一性和字符范围，再解析业务字段引用；不能仅凭模型重复的 ID 显示定位。原生 HTML 可作为文本查看；若渲染，沿用产品的 HTML 清理流程。

## 交付样例与协议文件

- [完整证据 JSON Schema](../design/37-document-parsing/document-evidence.schema.json)：Draft 2020-12，约束完整导出文件；不是工具分页结果的 Schema。`$defs.fileReference` 描述外层文件引用。
- [Paddle 合成样例](../design/37-document-parsing/document-evidence-paddle.example.json)
- [GLM 合成样例](../design/37-document-parsing/document-evidence-glm.example.json)
- [样例字节数与 SHA256](../design/37-document-parsing/document-evidence-examples.manifest.json)

这两个样例使用内置一页连接测试样例的已知文字，经过实际证据构建器生成。坐标是测试夹具，不是供应商在线结果；没有真实合同、账号或凭据，也没有实际上传。示例中的文件 ID 不能下载。清单中的 Hash 对应仓库中的 JSON 实际字节，用于客户端协议测试，不能当作在线上传验收记录。

本次只交付 DeepAgent 工具协议、证据文件与测试，不修改租赁前端、提示词或公共预览服务。租赁模型可继续输出原有业务字段、来源说明和置信度，再附 `(document_id,block_id)` 与证据文件引用；无需让模型重写全部 Block、坐标和原文。后续联调须先确认目标环境实际部署版本，代码已推送不等于 Alpha 已部署。
