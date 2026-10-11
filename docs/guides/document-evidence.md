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
| `export_file` | `false` | 后端将全部已取得的解析证据 JSON 与供应商处理页图打成一个 ZIP，上传当前 File Store |

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

ZIP 中的 `evidence.json` 同时保留固定布局视图、供应商原生页面正文和原生块正文／表格，便于复核；因此文件可能比模型读取内容大。不会把供应商整个 HTTP 响应直接上传。Base64、临时签名地址、运行机器路径和未白名单化的传输字段不导出。这里的原生正文是不做 Markdown 精简的正文，仍受上述传输信息清理规则约束。

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
| `file_name` | 后端生成的 `*-evidence.zip` 文件名 |
| `byte_size`、`mime` | 实际 ZIP 字节数；MIME 固定 `application/zip` |
| `sha256` | **整个 ZIP 原始字节**的 SHA256；不可解压后重打包再计算 |
| `schema_version` | 当前为 `document-evidence-v2`，与包内 `evidence.json.schema_version` 一致 |

导出使用当前服务端 File Store 及可信请求身份，没有用户可指定的上传地址或存储类型参数；沿用现有上传大小限制，不登记下载中心。Run 结束仅清理临时解析资源，成功导出的文件保留，其权限、保留与删除遵循原文件服务。

- `storage=local`：使用当前 DeepAgent 服务的 `GET /api/v1/local-files/{file_id}/download`，带当前会话身份；仅本地存储模式可用。`local_` ID 不能交给平台文件服务。
- `storage=platform`：通过当前平台已配置的文件服务 `/files/download?id={file_id}`，沿用现有鉴权与 App／Space 上下文。常见网关前缀为 `/deepfos-server/platform-file-server`，实际前缀由目标环境决定；不要硬编码 Alpha 域名。
- File ID、Hash、Block ID 都不授予访问权；工具每次续读仍验证原附件／Skill 来源，下载仍执行文件服务权限。

消费证据包时先按 `byte_size` 有界读取、核对整个 ZIP 的 `sha256`，再安全解压根条目 `evidence.json`，严格解码 UTF-8 并校验 Schema。文件内部的 `source.sha256` 校验的是**原附件文件字节**，不是 ZIP 或证据 JSON；`source.storage="skill"` 只描述源 Skill 资源，不改变导出文件引用中的存储类型。

Schema 之后还应核验 `document_id`、原文件引用／Hash、Block ID 唯一性和字符范围，再解析业务字段引用；不能仅凭模型重复的 ID 显示定位。原生 HTML 可作为文本查看；若渲染，沿用产品的 HTML 清理流程。

## 页图、坐标与单文件包

从 0.13.1 起，显式导出生成 `application/zip + document-evidence-v2`。历史 0.13.0 文件仍是 `application/json + document-evidence-v1`，历史文件不重写。消费方先支持两种 MIME／Schema 组合，再部署新后端；未升级的严格 JSON 客户端不能直接接收 ZIP。此次不增加运行时兼容开关，也不改变两个参数的默认值。

```text
doc_example-evidence.zip
├── evidence.json
└── pages/
    ├── page-0001.png
    └── page-0002.jpg
```

- `evidence.json` 保存完整正文、Block、源文件身份和现有几何信息；`pages[].image` 指向对应包内页图。文件名按 `page_ordinal` 生成，不能按 ZIP 条目顺序或文件名猜物理页码。
- Paddle 仅使用供应商顶层 `preprocessedImages` 与解析页建立的明确关联，标记 `role=processed_page, annotated=false`；不是 `layout_det_res` 等画了检测框的调试图，也不把原 PDF 渲染图冒充处理页图。签名 URL 仅在 Run 私有缓存使用，不写进 ZIP。
- GLM `layout_parsing` 请求设置 `need_layout_visualization=true`，保存其原生 `layout_visualization` 整页图，标记 `role=layout_visualization, annotated=true`。图上已有供应商绘制的框或标记，不能当作干净扫描底图。历史缓存或供应商未给出该资源时仍为 `not_provided`；不从图块资源、bbox 或原 PDF 猜图，不额外 OCR。只保存明确按页绑定的图，不猜缺失页对应关系。
- 原 PDF 保留来源引用与 SHA256，不在包内重复存储。没有增加原 PDF 图像配准，也没有新增原件定位已验证状态。

页图示例（合成数值）：

```json
{
  "image": {
    "status": "saved",
    "role": "processed_page",
    "annotated": false,
    "path": "pages/page-0001.png",
    "mime": "image/png",
    "byte_size": 10240,
    "sha256": "由后端根据实际图片字节计算的64位十六进制Hash",
    "width": 1224,
    "height": 1584,
    "coordinate_mapping": "matched"
  }
}
```

| 字段／状态 | 含义 |
| --- | --- |
| `image.status=saved` | 已保存通过格式、字节与像素检查的单帧 PNG／JPEG；不等于能定位 |
| `image.status=not_provided` | 未取得供应商明确提供的对应页图；报告 `reason_code` 与 `coordinate_mapping=unverified`，已知来源时可附 `role/annotated`，无伪造路径 |
| `image.status=failed` | 下载或安全检查失败，保留正文并报告固定 `reason_code`，无图片引用 |
| `coordinate_mapping=matched` | 来源明确为对应供应商的逐页产物、坐标空间正确、真实尺寸严格吻合且没有 EXIF 旋转；坐标属于这张图，不代表与原 PDF 同平面 |
| `role=processed_page, annotated=false` | Paddle 原生预处理页图。与 `matched` 组合时，消费方可以将它作为干净底图叠加所选 Block 框 |
| `role=layout_visualization, annotated=true` | GLM 原生带框整页图。可展示其原生框；`matched` 时可额外聚焦所选位置，但不可声称是干净底图，也不能移除已烘焙进图中的原生框 |
| `coordinate_mapping=dimension_mismatch` | 图可保存，但真实尺寸与供应商坐标页尺寸不符，不能直接叠框 |
| `coordinate_mapping=unverified` | 坐标空间、尺寸或 EXIF 等条件未证明吻合，不能直接叠框 |

`matched` 的单位是图片原始像素，原点左上角，x 向右、y 向下。按显示宽高同比例映射即可；不要再应用 `preprocessing.angle` 旋转图片或框。只有尺寸相同不足以确认关系，还必须有供应商逐页图来源关联。包内所有图片哈希、实际尺寸和声明 MIME 都需校验，EXIF 非默认方向时后端不会标记 `matched`。

顶层 `images` 单独记录 `status=complete/partial/unavailable`、`expected`（已解析页面数）、`saved` 和 `missing_page_ordinals`。`complete` 只表示每个已解析页有图片，不表示原文不缺页、所有框正确或均可定位；OCR 完整性继续看 `status/summary`。单图缺失不改变已有正文或提升 OCR 完整性。

`reason_code` 使用封闭枚举，Schema 拒绝任意其他字符串，不承载上游异常、正文、凭据或 URL：

| 图片状态／坐标状态 | 允许的 `reason_code` |
| --- | --- |
| `not_provided` | 仅 `not_provided` |
| `failed` | `unsupported_image_source`、`download_timeout`、`download_budget_exceeded`、`unsafe_url`、`artifact_access_denied`、`result_too_large`、`rate_limited`、`network_error`、`service_error`、`permission_denied`、`invalid_image`、`image_pixels_exceeded`、`unsupported_image_format`、`animated_image`、`image_download_failed` |
| `saved` 且 `unverified` | `exif_orientation`、`coordinate_space_unverified`、`page_dimensions_unknown` |
| `saved` 且 `dimension_mismatch` | 仅 `dimension_mismatch` |
| `saved` 且 `matched` | 不允许出现 `reason_code` |

缺图、失败和保存成功但未匹配都必须给原因码；未知异常统一映射为 `image_download_failed`，不复制异常文本。

## 导出限制与客户端校验

仅在 `export_file=true` 时下载页图；`include_layout=true` 本身不下载。下载和打包包含在同一 Run 的导出单飞中，分页、重复读取、先读后导出均不重复 OCR 或上传。图片下载失败后仍可保存包含完整已解析正文的包，具体缺图情况看包内 `images`；已保存包不因续读悄悄补图、改 Hash 或重新上传。

| 限制 | 当前规则 |
| --- | --- |
| 最终 ZIP | 不超过现有 `upload_max_size_mb` 对应字节数 |
| 累计解压大小 | `evidence.json` 未压缩字节数 + 所有图片原字节数，同样不超过该上限 |
| 单图 | 最多 20 MiB、3200 万像素，PNG／JPEG 单帧，验证真实解码 |
| 下载等待 | 整个页图收集阶段最多 180 秒，取消沿用 Run 取消 |
| 条目 | 导出包最多 1000 个已解析页、1001 个条目；超过则 `bundle_page_limit`，不截断解析正文或悄悄丢页 |

成功保存图片与 JSON 的合计受包大小约束；失败 GET 的重试可能额外消耗网络流量，总网络流量不保证等于最终包大小。

未覆盖配置时，现有上传上限默认为 20 MiB；目标环境以服务端实际配置为准，本轮不新增或上调配置。长文档可能因图片预算不足得到 `images.status=partial`；完整证据 JSON 自身或最终包超限时导出失败，工具正文仍可继续读取。不要把前端旧的 16 MiB JSON 限制直接套到新包而不显示明确错误。

消费方必须限制实际读入／解压字节与图片解码像素，不能只相信 ZIP 元数据。拒绝加密条目、符号链接、目录穿越、绝对路径、反斜杠和重名条目；只接受根 `evidence.json` 及符合 `pages/page-NNNN.jpg|png` 的图片路径，不执行 HTML／SVG／脚本。不允许引用包外 URL。条目集合应与 JSON 声明图片一致，每图最多引用一次；验证图片字节数、Hash、真实格式和尺寸后再创建 Blob。换文件或关闭视图释放 Blob／解码资源。

整包 Hash、身份或 Schema 校验失败时拒绝使用；单图校验失败时保留可信正文并显示该页无法定位。关联业务字段仍校验 `document_id/block_id`，不以最后一次工具调用、文件名或条目排序猜来源。

## 交付样例与协议文件

- [当前证据 JSON Schema](../design/37-document-parsing/document-evidence.schema.json)：Draft 2020-12，约束 ZIP 内 `evidence.json`；不是工具分页结果的 Schema。`$defs.fileReference` 描述外层 ZIP 文件引用。
- [Paddle 合成 ZIP](../design/37-document-parsing/document-evidence-paddle.example.zip)／[可读 JSON 副本](../design/37-document-parsing/document-evidence-paddle.example.json)：含一张受控测试页图，`matched` 只针对该合成图。
- [GLM 合成 ZIP](../design/37-document-parsing/document-evidence-glm.example.zip)／[可读 JSON 副本](../design/37-document-parsing/document-evidence-glm.example.json)：含一张明确标注为合成夹具的带框页图，`role=layout_visualization, annotated=true`；测试另覆盖旧缓存无图时的 `not_provided`。
- [样例包字节数与 SHA256](../design/37-document-parsing/document-evidence-examples.manifest.json)
- [v1 历史 Schema 与样例](../design/37-document-parsing/history/document-evidence-v1/README.md)

样例文字沿用内置一页连接测试文件，GLM 图中带框并写明 `SYNTHETIC LAYOUT VISUALIZATION`。页图、坐标和供应商响应是受控夹具，经实际打包器生成；不是在线 OCR、真实合同或实际上传。示例文件 ID 不能下载。清单 Hash 对应 ZIP 实际字节；可读 JSON 副本仅供查看，解析后与包内 JSON 一致。

本次只交付 DeepAgent 工具协议、证据包与测试，不修改租赁前端、提示词或公共预览服务。租赁模型继续输出原有业务字段、来源说明和置信度，再附 `(document_id,block_id)` 与证据文件引用；无需让模型重写全部 Block、坐标、原文或图片。消费侧先兼容 ZIP/v2 再部署后端；代码推送或 Tag 不等于 Alpha 已部署，联调须核验目标环境版本。
